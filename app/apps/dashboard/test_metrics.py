"""UI-02: показатели главной страницы — на синтетических данных."""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.attendance.models import GroupMeeting, MeetingKind
from apps.dashboard import metrics, selectors
from apps.documents.models import ProjectBrief
from apps.flows.models import Flow, Group
from apps.interns.models import GraduateStatus, Intern, InternStatus, Position
from apps.projects.models import Project, ProjectReport, ProjectStageKey, ProjectStatus
from apps.reports.models import WeeklyReport
from apps.teams.models import TeamMember, TeamRole
from apps.training.models import Specialization

UserModel = get_user_model()
D = datetime.date
TODAY = D(2026, 10, 21)  # среда
LAST_WEEK = metrics.Week(D(2026, 10, 12), D(2026, 10, 18), complete=True)


def member(project, intern, role=TeamRole.BACKEND, **kw):
    return TeamMember.objects.create(project=project, intern=intern, role=role, **kw)


class WeekHelpersTests(TestCase):
    def test_last_complete_week_is_previous_monday_to_sunday(self):
        for today in (D(2026, 10, 19), D(2026, 10, 21), D(2026, 10, 25)):  # пн, ср, вс
            week = metrics.last_complete_week(today)
            self.assertEqual((week.start, week.end), (D(2026, 10, 12), D(2026, 10, 18)))
            self.assertTrue(week.complete)
            self.assertEqual(week.start.weekday(), 0)

    def test_current_week_is_marked_incomplete(self):
        week = metrics.week_of(TODAY, TODAY)
        self.assertFalse(week.complete)
        self.assertIn('неполная', week.label)

    def test_parse_week(self):
        self.assertEqual(metrics.parse_week('garbage', TODAY), metrics.last_complete_week(TODAY))
        self.assertEqual(metrics.parse_week('2026-10-15', TODAY).start, D(2026, 10, 12))
        future = metrics.parse_week('2027-01-01', TODAY)
        self.assertEqual(future.start, D(2026, 10, 19))  # дальше текущей недели не уходим
        self.assertFalse(future.complete)


class PeopleKpiTests(TestCase):
    def setUp(self):
        self.done = Project.objects.create(name='Сданный', status=ProjectStatus.COMPLETED, actual_end_date=D(2026, 9, 1))
        self.p1 = Project.objects.create(name='Первый')
        self.p2 = Project.objects.create(name='Второй')

    def test_graduate_on_two_projects_counted_once(self):
        grad = Intern.objects.create(full_name='Выпускник Двух', status=InternStatus.ACTIVE)
        member(self.done, grad, status=TeamMember.Status.LEFT, left_at=D(2026, 9, 1), left_reason=TeamMember.LeftReason.PROJECT_ENDED)
        member(self.p1, grad)
        member(self.p2, grad)
        novice = Intern.objects.create(full_name='Новичок Один', status=InternStatus.ACTIVE)
        member(self.p1, novice)
        kpi = metrics.people_kpi()
        self.assertEqual(kpi['active'], 2)
        self.assertEqual(kpi['old'], 1)
        self.assertEqual(kpi['new'], 1)
        self.assertEqual(kpi['graduates'], 1)
        self.assertEqual(kpi['active_participations'], 3)  # участия ≠ люди

    def test_graduate_status_survives_new_assignment(self):
        """graduate_status сброшен при назначении, но завершённая стажировка была."""
        grad = Intern.objects.create(full_name='Вернулся', status=InternStatus.ACTIVE, graduate_status='')
        member(self.done, grad, status=TeamMember.Status.LEFT, left_at=D(2026, 9, 1),
               left_reason=TeamMember.LeftReason.PROJECT_ENDED)
        member(self.p1, grad)
        self.assertEqual(metrics.people_kpi()['old'], 1)

    def test_graduation_signals(self):
        """UI-02.1: повторная стажировка — не выпуск; «Проект завершён» у
        незакрытого проекта — на сверку, не выпуск."""
        by_leave = Intern.objects.create(full_name='Снят Завершён')
        member(self.p1, by_leave, status=TeamMember.Status.LEFT, left_reason=TeamMember.LeftReason.PROJECT_ENDED, left_at=TODAY)
        by_attempt = Intern.objects.create(full_name='Вторая Попытка', internship_attempt=2)
        early = Intern.objects.create(full_name='Ушёл Досрочно')
        member(self.done, early, status=TeamMember.Status.LEFT, left_reason=TeamMember.LeftReason.SELF, left_at=D(2026, 8, 1))
        confirmed = Intern.objects.create(full_name='Отметка Есть', graduate_status=GraduateStatus.DECLINED)
        grad = metrics.graduation()
        self.assertNotIn(by_leave.pk, grad.ids)
        self.assertNotIn(by_attempt.pk, grad.ids)
        self.assertNotIn(early.pk, grad.ids)
        self.assertIn(confirmed.pk, grad.ids)
        review = {item['intern_id'] for item in grad.review}
        self.assertTrue({by_leave.pk, by_attempt.pk} <= review)
        self.assertNotIn(early.pk, review)  # досрочный выход однозначен — сверять нечего

    def test_leads_and_archived_are_not_interns(self):
        Intern.objects.create(full_name='Тимлид Сотрудник', status=InternStatus.ACTIVE, position=Position.TEAM_LEAD)
        Intern.objects.create(full_name='В Архиве', status=InternStatus.ACTIVE, is_archived=True)
        Intern.objects.create(full_name='Замороженный', status=InternStatus.PAUSED)
        self.assertEqual(metrics.people_kpi()['active'], 0)


class BranchTableTests(TestCase):
    def test_split_by_branch_and_direction_without_duplicates(self):
        backend = Specialization.objects.create(name='Backend')
        frontend = Specialization.objects.create(name='Frontend')
        done = Project.objects.create(name='Сданный', status=ProjectStatus.COMPLETED, actual_end_date=D(2026, 9, 1))
        p1, p2 = Project.objects.create(name='А'), Project.objects.create(name='Б')
        a = Intern.objects.create(full_name='А Бишкек', branch='Бишкек', specialization=backend, status=InternStatus.ACTIVE)
        member(p1, a)
        member(p2, a)  # два проекта — один человек
        b = Intern.objects.create(full_name='Б Бишкек', branch='Бишкек', specialization=backend, status=InternStatus.ACTIVE)
        member(done, b, status=TeamMember.Status.LEFT, left_at=D(2026, 9, 1), left_reason=TeamMember.LeftReason.PROJECT_ENDED)
        Intern.objects.create(full_name='В Ош', city='г. Ош', specialization=frontend, status=InternStatus.ACTIVE)
        table = metrics.branch_table()
        bishkek = next(b for b in table['branches'] if b['name'] == 'Бишкек')
        osh = next(b for b in table['branches'] if b['name'] == 'Ош')
        line = bishkek['lines'][0]
        self.assertEqual((line['name'], line['new'], line['old'], line['total']), ('Backend', 1, 1, 2))
        self.assertEqual(osh['totals'], {'new': 1, 'old': 0, 'total': 1})
        self.assertEqual(table['totals'], {'new': 2, 'old': 1, 'total': 3})


class StageCategoryTests(TestCase):
    def test_every_stage_maps_to_exactly_one_category(self):
        seen = {}
        for key, _label, stages in metrics.STAGE_CATEGORIES:
            for stage in stages:
                self.assertNotIn(stage, seen, f'{stage} в двух категориях')
                seen[stage] = key
        self.assertEqual(set(seen), set(ProjectStageKey.values))

    def _with_primary(self, name, key, **fields):
        from apps.projects.models import ProjectStage

        project = Project.objects.create(name=name, **fields)
        stage = ProjectStage.objects.create(project=project, key=key)
        Project.objects.filter(pk=project.pk).update(primary_stage=stage)
        return project

    def test_projects_counted_once_by_primary_stage(self):
        old_new = self._with_primary('Старый новый', ProjectStageKey.NEW)
        Project.objects.filter(pk=old_new.pk).update(created_at=timezone.now() - datetime.timedelta(days=60))
        self._with_primary('Свежий в разработке', ProjectStageKey.BACKEND)
        self._with_primary('Тест', ProjectStageKey.TESTING)
        self._with_primary('Сдача', ProjectStageKey.DELIVERY)
        self._with_primary('Готов', ProjectStageKey.BACKEND, status=ProjectStatus.COMPLETED)  # завершён — отдельно
        self._with_primary('Отменён', ProjectStageKey.BACKEND, status=ProjectStatus.CANCELLED)
        data = metrics.projects_by_stage()
        counts = {c['key']: c['count'] for c in data['categories']}
        self.assertEqual(counts, {'new': 1, 'development': 1, 'testing': 1, 'delivery': 1, 'completed': 1})
        self.assertEqual(data['total'], 5)
        self.assertEqual(data['stopped'], 1)
        self.assertEqual(data['unconfirmed'], 0)
        self.assertEqual(sum(counts.values()), data['total'])  # ни один проект не учтён дважды

    def test_without_primary_stage_counted_once_and_marked_unconfirmed(self):
        Project.objects.create(name='Без выбора', current_stage=ProjectStageKey.TESTING)
        data = metrics.projects_by_stage()
        testing = next(c for c in data['categories'] if c['key'] == 'testing')
        self.assertEqual((testing['count'], testing['unconfirmed'], data['unconfirmed']), (1, 1, 1))

    def test_parallel_stages_project_in_one_category(self):
        from apps.projects.models import ProjectStage

        project = Project.objects.create(name='Параллельный', current_stage=ProjectStageKey.DESIGN)
        backend = ProjectStage.objects.create(project=project, key=ProjectStageKey.BACKEND, status=ProjectStage.Status.IN_PROGRESS)
        ProjectStage.objects.create(project=project, key=ProjectStageKey.FRONTEND, status=ProjectStage.Status.IN_PROGRESS)
        ProjectStage.objects.create(project=project, key=ProjectStageKey.TESTING, status=ProjectStage.Status.IN_PROGRESS)
        Project.objects.filter(pk=project.pk).update(primary_stage=backend)
        counts = {c['key']: c['count'] for c in metrics.projects_by_stage()['categories']}
        self.assertEqual(sum(counts.values()), 1)
        self.assertEqual(counts['development'], 1)

    def test_project_kpi(self):
        Project.objects.create(name='А')
        Project.objects.create(name='Б', status=ProjectStatus.COMPLETED)
        Project.objects.create(name='В', status=ProjectStatus.PAUSED)
        Project.objects.create(name='Г', is_archived=True)
        self.assertEqual(metrics.project_kpi(), {'total': 3, 'active': 1, 'paused': 1, 'completed': 1, 'stopped': 0})


class WeeklyActivityTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Проект')

    def test_registration_uses_bishkek_dates_and_week_bounds(self):
        tz = timezone.get_current_timezone()
        inside = Intern.objects.create(full_name='Воскресенье Поздно')
        outside = Intern.objects.create(full_name='Понедельник Рано')
        Intern.objects.filter(pk=inside.pk).update(created_at=datetime.datetime(2026, 10, 18, 23, 30, tzinfo=tz))
        Intern.objects.filter(pk=outside.pk).update(created_at=datetime.datetime(2026, 10, 19, 0, 10, tzinfo=tz))
        self.assertEqual(metrics.weekly_activity(LAST_WEEK, TODAY)['registered'], 1)

    def test_first_graduation_only(self):
        first = Project.objects.create(name='Первый', status=ProjectStatus.COMPLETED, actual_end_date=D(2026, 10, 14))
        again = Intern.objects.create(full_name='Повторный')
        old = Project.objects.create(name='Давний', status=ProjectStatus.COMPLETED, actual_end_date=D(2026, 6, 1))
        member(old, again, status=TeamMember.Status.LEFT, left_at=D(2026, 6, 1), left_reason=TeamMember.LeftReason.PROJECT_ENDED)
        member(first, again, status=TeamMember.Status.LEFT, left_at=D(2026, 10, 14), left_reason=TeamMember.LeftReason.PROJECT_ENDED)
        fresh = Intern.objects.create(full_name='Впервые')
        member(first, fresh, status=TeamMember.Status.LEFT, left_at=D(2026, 10, 14), left_reason=TeamMember.LeftReason.PROJECT_ENDED)
        boundary = Intern.objects.create(full_name='Граница')
        member(self.project, boundary, status=TeamMember.Status.LEFT,
               left_reason=TeamMember.LeftReason.PROJECT_ENDED, left_at=D(2026, 10, 19))  # уже следующая неделя
        self.assertEqual(metrics.weekly_activity(LAST_WEEK, TODAY)['graduated'], 1)

    def test_exits_are_people_not_memberships(self):
        p2 = Project.objects.create(name='Второй')
        quitter = Intern.objects.create(full_name='Сам Ушёл')
        for project in (self.project, p2):
            member(project, quitter, status=TeamMember.Status.LEFT, left_reason=TeamMember.LeftReason.SELF, left_at=D(2026, 10, 13))
        kicked = Intern.objects.create(full_name='Не Подошёл')
        member(self.project, kicked, status=TeamMember.Status.LEFT, left_reason=TeamMember.LeftReason.NOT_FIT, left_at=D(2026, 10, 18))
        silent = Intern.objects.create(full_name='Не Отвечает')
        member(self.project, silent, status=TeamMember.Status.LEFT, left_reason=TeamMember.LeftReason.UNRESPONSIVE, left_at=D(2026, 10, 11))
        data = metrics.weekly_activity(LAST_WEEK, TODAY)
        self.assertEqual((data['self_left'], data['expelled']), (1, 1))

    def test_freezes_from_audit_log(self):
        from apps.audit.services import log
        from apps.interns.services import pause_person

        a = Intern.objects.create(full_name='Заморожен Кнопкой', status=InternStatus.ACTIVE)
        b = Intern.objects.create(full_name='Заморожен Формой', status=InternStatus.ACTIVE)
        with mock.patch('django.utils.timezone.now', return_value=datetime.datetime(2026, 10, 14, 12, tzinfo=datetime.timezone.utc)):
            pause_person(a)
            log(b, 'Статус изменён', old_value='Активный', new_value='Приостановлен')
            log(b, 'Статус изменён', old_value='Приостановлен', new_value='Активный')
        self.assertEqual(metrics.weekly_activity(LAST_WEEK, TODAY)['frozen'], 2)

    def test_completed_projects_by_actual_end_date(self):
        Project.objects.create(name='В срок', status=ProjectStatus.COMPLETED, actual_end_date=D(2026, 10, 18))
        Project.objects.create(name='Позже', status=ProjectStatus.COMPLETED, actual_end_date=D(2026, 10, 19))
        self.assertEqual(metrics.weekly_activity(LAST_WEEK, TODAY)['projects_completed'], 1)

    def test_meetings(self):
        flow = Flow.objects.create(number=77)
        group = Group.objects.create(flow=flow, number=1, project=self.project)
        GroupMeeting.objects.create(group=group, date=D(2026, 10, 13), status=GroupMeeting.Status.HELD)
        GroupMeeting.objects.create(group=group, date=D(2026, 10, 14), status=GroupMeeting.Status.MISSED)
        GroupMeeting.objects.create(group=group, date=D(2026, 10, 15), status=GroupMeeting.Status.PLANNED)
        GroupMeeting.objects.create(group=group, date=D(2026, 10, 16), kind=MeetingKind.OTHER, status=GroupMeeting.Status.MISSED)
        m = metrics.weekly_activity(LAST_WEEK, TODAY)['meetings']
        self.assertEqual((m['held'], m['missed'], m['missed_unmarked']), (1, 2, 1))
        current = metrics.week_of(TODAY, TODAY)
        GroupMeeting.objects.create(group=group, date=D(2026, 10, 23), status=GroupMeeting.Status.PLANNED)
        self.assertEqual(metrics.weekly_activity(current, TODAY)['meetings']['upcoming'], 1)


class ReportRulesTests(TestCase):
    def make_pm(self, name, *projects):
        user = UserModel.objects.create_user(username=name, password='x', role=User.Role.PROJECT_MANAGER)
        card = Intern.objects.create(full_name=name, user=user)
        for project in projects:
            member(project, card, role=TeamRole.PROJECT_MANAGER, joined_at=D(2026, 9, 1))
        return user

    def report(self, user, project, day):
        report = ProjectReport.objects.create(project=project, text='отчёт', author=user)
        ProjectReport.objects.filter(pk=report.pk).update(date=day)

    def test_pm_one_report_per_week_friday_or_saturday(self):
        a, b = Project.objects.create(name='А'), Project.objects.create(name='Б')
        on_time = self.make_pm('pm-friday', a, b)  # два проекта — всё равно один отчёт
        late = self.make_pm('pm-wednesday', a)
        self.make_pm('pm-none', b)
        self.report(on_time, a, D(2026, 10, 16))
        self.report(late, a, D(2026, 10, 14))
        r = metrics.pm_reports(LAST_WEEK, TODAY)
        self.assertEqual((r['expected'], r['on_time'], r['other_day'], r['missing']), (3, 1, 1, 1))
        self.assertTrue(r['due'])

    def test_pm_deadline_not_reached_in_current_week(self):
        a = Project.objects.create(name='А')
        self.make_pm('pm-current', a)
        r = metrics.pm_reports(metrics.week_of(TODAY, TODAY), TODAY)
        self.assertIsNone(r['missing'])
        self.assertFalse(r['due'])

    def test_lead_obligations_are_per_project(self):
        a, b = Project.objects.create(name='А'), Project.objects.create(name='Б')
        lead = Intern.objects.create(full_name='Тимлид', position=Position.TEAM_LEAD)
        member(a, lead, role=TeamRole.TEAM_LEAD)
        member(b, lead, role=TeamRole.TEAM_LEAD)
        r = metrics.lead_reports(LAST_WEEK, TODAY)
        self.assertEqual(r['expected'], 2)
        # этап 7B: отчёты сдаются; за завершённую неделю без отчётов — 2 не сдано
        self.assertEqual((r['submitted'], r['missing']), (0, 2))


class CompareWeeksTests(TestCase):
    def test_event_rows_have_delta_and_percent(self):
        Project.objects.create(name='Эта', status=ProjectStatus.COMPLETED, actual_end_date=D(2026, 10, 15))
        Project.objects.create(name='Эта2', status=ProjectStatus.COMPLETED, actual_end_date=D(2026, 10, 16))
        Project.objects.create(name='Прошлая', status=ProjectStatus.COMPLETED, actual_end_date=D(2026, 10, 7))
        data = metrics.compare_weeks(LAST_WEEK, TODAY)
        self.assertEqual(data['previous'].start, D(2026, 10, 5))
        row = next(r for r in data['rows'] if r['key'] == 'projects_completed')
        self.assertEqual((row['current'], row['previous'], row['delta'], row['percent']), (2, 1, 1, 100.0))
        zero = next(r for r in data['rows'] if r['key'] == 'registered')
        self.assertIsNone(zero['percent'])  # база 0 — процента нет

    def test_state_rows_need_snapshots(self):
        data = metrics.compare_weeks(LAST_WEEK, TODAY)
        state = [r for r in data['rows'] if r['kind'] == 'state']
        self.assertTrue(all(r['current'] is None and r['previous'] is None for r in state))
        self.assertFalse(data['has_state'])

    def test_state_rows_from_snapshots(self):
        WeeklyReport.objects.create(week_start=D(2026, 10, 12), data={'dashboard_state': {'active_interns': 12, 'active_projects': 4}})
        WeeklyReport.objects.create(week_start=D(2026, 10, 5), data={'interns': {'active': 10}, 'projects': {'active': 4}})
        rows = {r['key']: r for r in metrics.compare_weeks(LAST_WEEK, TODAY)['rows']}
        self.assertEqual((rows['active_interns']['current'], rows['active_interns']['previous'], rows['active_interns']['delta']), (12, 10, 2))
        self.assertEqual(rows['active_projects']['percent'], 0.0)
        self.assertIsNone(rows['old_interns']['previous'])


class WeeklySnapshotTests(TestCase):
    def test_snapshot_only_for_just_finished_week_and_preserved(self):
        from apps.reports.services import generate_weekly_report

        with mock.patch('django.utils.timezone.localdate', return_value=TODAY):
            report = generate_weekly_report(TODAY)
            self.assertIn('dashboard_state', report.data)
            older = generate_weekly_report(D(2026, 9, 1))
            self.assertNotIn('dashboard_state', older.data)
            report.data['dashboard_state']['active_interns'] = 99
            report.save()
            again = generate_weekly_report(TODAY)
            self.assertEqual(again.data['dashboard_state']['active_interns'], 99)


class AttentionTests(TestCase):
    def test_events_link_to_objects(self):
        project = Project.objects.create(name='Без команды', planned_end_date=timezone.localdate() - datetime.timedelta(days=2))
        flow = Flow.objects.create(number=78)
        group = Group.objects.create(flow=flow, number=1, project=project)
        meeting = GroupMeeting.objects.create(group=group, date=timezone.localdate() - datetime.timedelta(days=3),
                                              status=GroupMeeting.Status.MISSED)
        ProjectBrief.objects.create(project_name='Заявка')
        items = selectors.attention_items()
        texts = {i['text']: i['url'] for i in items}
        self.assertIn(project.get_absolute_url() + '?tab=team', [u for t, u in texts.items() if 'не назначен ПМ' in t])
        self.assertTrue(any('не назначен тимлид' in t for t in texts))
        self.assertTrue(any('дедлайн прошёл' in t for t in texts))
        self.assertIn(reverse('attendance:meeting_detail', args=[meeting.pk]), texts.values())
        self.assertIn(reverse('documents:brief_requests'), texts.values())
        self.assertFalse(any('Проблемн' in t for t in texts))
        self.assertEqual(items[0]['level'], 'danger')

    def test_missing_pm_report_appears(self):
        project = Project.objects.create(name='С ПМ')
        user = UserModel.objects.create_user(username='pm-att', password='x', role=User.Role.PROJECT_MANAGER)
        card = Intern.objects.create(full_name='ПМ Без Отчёта', user=user)
        member(project, card, role=TeamRole.PROJECT_MANAGER)
        items = selectors.attention_items()
        self.assertTrue(any('ПМ Без Отчёта' in i['text'] and 'нет отчёта ПМ' in i['text'] for i in items))


class DashboardViewTests(TestCase):
    def setUp(self):
        self.head = UserModel.objects.create_user(username='dash-head', password='x', role=User.Role.HEAD)
        self.client.force_login(self.head)

    def test_page_renders_new_design_with_real_numbers(self):
        Intern.objects.create(full_name='Активный Один', status=InternStatus.ACTIVE, branch='Ош')
        response = self.client.get(reverse('dashboard'))
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('gp-legacy', html)
        self.assertNotIn('Проблемн', html)
        for label in ('Активные стажёры', 'Новые стажёры', 'Старые стажёры', 'Выпускники', 'Всего проектов',
                      'Активные проекты', 'Завершённые проекты', 'Проекты по основному этапу',
                      'Требует внимания', 'Сравнить с прошлой неделей', 'Неделя'):
            self.assertIn(label, html)
        self.assertEqual(response.context['people']['active'], 1)

    def test_all_links_on_page_work(self):
        import re

        Project.objects.create(name='Проект', current_stage=ProjectStageKey.BACKEND)
        html = self.client.get(reverse('dashboard')).content.decode()
        main = html[html.index('id="gp-main"'):]
        hrefs = {h for h in re.findall(r'href="(/[^"#]*)', main) if not h.startswith(('/static/', '/media/'))}
        self.assertGreater(len(hrefs), 5)
        for href in sorted(hrefs):
            with self.subTest(href=href):
                self.assertIn(self.client.get(href.replace('&amp;', '&')).status_code, (200, 302))

    def test_week_fragment_and_compare(self):
        r = self.client.get(reverse('dashboard_week') + '?week=2026-10-12', HTTP_HX_REQUEST='true')
        self.assertNotIn('gp-shell', r.content.decode())
        self.assertContains(r, 'id="week-block"')
        r = self.client.get(reverse('dashboard_compare'), HTTP_HX_REQUEST='true')
        last = metrics.last_complete_week()
        self.assertContains(r, last.label)
        self.assertContains(r, metrics.week_of(last.start - datetime.timedelta(days=7)).label)
        self.assertContains(r, 'Нет данных')
        self.assertEqual(self.client.get(reverse('dashboard_compare')).status_code, 200)

    def test_query_count_does_not_grow_with_data(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse('dashboard'))
            return len(ctx.captured_queries)

        # База проходит те же ветки кода (проект на передаче, активный стажёр),
        # что и большой набор, — иначе разница была бы в ветках, а не в N+1.
        first = Project.objects.create(name='Один', current_stage=ProjectStageKey.DELIVERY)
        member(first, Intern.objects.create(full_name='Первый', status=InternStatus.ACTIVE, branch='Ош'))
        base = count()
        for n in range(15):
            project = Project.objects.create(name=f'Проект {n}', current_stage=ProjectStageKey.DELIVERY)
            intern = Intern.objects.create(full_name=f'Стажёр {n}', status=InternStatus.ACTIVE, branch='Бишкек')
            member(project, intern)
        self.assertEqual(count(), base)

    def test_access(self):
        intern = UserModel.objects.create_user(username='dash-intern', password='x', role=User.Role.INTERN)
        self.client.force_login(intern)
        for url in (reverse('dashboard'), reverse('dashboard_week'), reverse('dashboard_compare')):
            self.assertEqual(self.client.get(url).status_code, 403)
        pm = UserModel.objects.create_user(username='dash-pm', password='x', role=User.Role.PROJECT_MANAGER)
        self.client.force_login(pm)
        self.assertRedirects(self.client.get(reverse('dashboard_compare')), reverse('pm_portal:dashboard'),
                             fetch_redirect_response=False)


class InternStatusAuditTests(TestCase):
    def test_status_change_in_form_is_logged(self):
        from apps.audit.models import AuditLog

        head = UserModel.objects.create_user(username='audit-head', password='x', role=User.Role.HEAD)
        intern = Intern.objects.create(full_name='Меняем Статус', status=InternStatus.ACTIVE)
        self.client.force_login(head)
        response = self.client.get(reverse('interns:update', args=[intern.pk]))
        form = response.context['form']
        data = {k: ('' if v is None else v) for k, v in form.initial.items() if k in form.fields}
        data.update({'full_name': intern.full_name, 'status': InternStatus.PAUSED, 'internship_attempt': 1})
        data = {k: (v.pk if hasattr(v, 'pk') else v) for k, v in data.items()}
        self.client.post(reverse('interns:update', args=[intern.pk]), data)
        intern.refresh_from_db()
        self.assertEqual(intern.status, InternStatus.PAUSED)
        log = AuditLog.objects.get(object_type='Intern', object_id=str(intern.pk), action='Статус изменён')
        self.assertEqual(log.new_value, 'Приостановлен')

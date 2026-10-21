"""Этап 7B: еженедельные оценки, отчёты тимлидов и ПМ, показатели сдачи."""
import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.dashboard import metrics
from apps.interns.models import Intern, InternEvaluation, InternStatus
from apps.notifications.models import Notification
from apps.projects.fixtures_closure import head
from apps.projects.models import Project
from apps.projects.services import create_project
from apps.reports import weekly
from apps.reports.models import LeadWeeklyReport, PmWeeklyReport, WeeklyEvaluation
from apps.teams import staff
from apps.teams.models import TeamMember, TeamRole
from apps.training.models import Specialization

UserModel = get_user_model()
D = datetime.date
WED = D(2026, 10, 21)          # среда
FRI = D(2026, 10, 23)
MON = D(2026, 10, 19)          # начало текущей недели
LAST_MON = D(2026, 10, 12)


class Base(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.frontend = Specialization.objects.create(name='Frontend')
        self.project = create_project(Project(name='Отчётный', recruitment_open=True))
        self.second = create_project(Project(name='Второй', recruitment_open=True))
        self.lead_user, self.lead = self.make_lead('r7-lead', 'Тимлид Бэка')
        for project in (self.project, self.second):
            staff.assign_lead(project, self.lead, self.head)
        TeamMember.objects.filter(intern=self.lead).update(joined_at=D(2026, 9, 1))
        self.pm_user = UserModel.objects.create_user(username='r7-pm', password='x', role=User.Role.PROJECT_MANAGER)
        self.pm = Intern.objects.create(full_name='ПМ Отчётов', user=self.pm_user)
        TeamMember.objects.create(project=self.project, intern=self.pm, role=TeamRole.PROJECT_MANAGER,
                                  status='active', joined_at=D(2026, 9, 1))
        self.intern = self.member('Стажёр Бэка', self.backend, TeamRole.BACKEND, self.project, self.second)
        self.front = self.member('Стажёр Фронта', self.frontend, TeamRole.FRONTEND, self.project)

    def make_lead(self, username, name):
        user = UserModel.objects.create_user(username=username, password='x', role=User.Role.TEAM_LEAD)
        lead = Intern.objects.create(full_name=name, user=user, specialization=self.backend, position='lead',
                                     staff_role='lead', staff_status='active')
        return user, lead

    def member(self, name, spec, role, *projects):
        intern = Intern.objects.create(full_name=name, specialization=spec, status=InternStatus.ACTIVE)
        for project in projects:
            TeamMember.objects.create(project=project, intern=intern, role=role, status='active')
        return intern


class LeadReportTests(Base):
    def test_weekly_once_extra_any_and_rights(self):
        report = weekly.submit_lead_report(self.lead_user, self.project, 'Неделя прошла хорошо', today=WED)
        self.assertEqual((report.week_start, report.direction, report.kind), (MON, 'backend', 'weekly'))
        with self.assertRaises(weekly.ReportError):
            weekly.submit_lead_report(self.lead_user, self.project, 'второй обязательный', today=WED)
        weekly.submit_lead_report(self.lead_user, self.project, 'по запросу ПМ', kind='extra', today=WED)
        weekly.submit_lead_report(self.lead_user, self.project, 'ещё', kind='extra', today=FRI)
        self.assertEqual(LeadWeeklyReport.objects.filter(kind='weekly').count(), 1)
        other = create_project(Project(name='Не мой'))
        for user, project in ((self.lead_user, other), (self.pm_user, self.project), (self.head, self.project)):
            with self.assertRaises(PermissionDenied):
                weekly.submit_lead_report(user, project, 'текст', today=WED)
        with self.assertRaises(weekly.ReportError):
            weekly.submit_lead_report(self.lead_user, self.project, 'старая', week_start=D(2026, 10, 5), today=WED)
        with self.assertRaises(IntegrityError), transaction.atomic():  # гонка двух запросов
            LeadWeeklyReport.objects.create(lead=self.lead, project=self.project, direction='backend',
                                            week_start=MON, text='дубль')

    def test_submitted_pending_missed(self):
        current = metrics.week_of(WED, WED)
        last = metrics.week_of(LAST_MON, WED)
        weekly.submit_lead_report(self.lead_user, self.project, 'сдан', today=WED)
        status = {o.project.pk: o.status for o in weekly.lead_obligations(current, WED)}
        self.assertEqual(status, {self.project.pk: 'submitted', self.second.pk: 'pending'})  # пропуск не заранее
        self.assertEqual({o.status for o in weekly.lead_obligations(last, WED)}, {'missed'})
        r = metrics.lead_reports(current, WED)
        self.assertEqual((r['expected'], r['submitted'], r['missing'], r['pending']), (2, 1, None, 1))
        late = weekly.submit_lead_report(self.lead_user, self.second, 'досдаю', week_start=LAST_MON, today=WED)
        self.assertEqual(late.week_start, LAST_MON)  # created_at — фактическое время подачи
        self.assertEqual(metrics.lead_reports(last, WED)['missing'], 1)

    def test_replaced_lead_keeps_history(self):
        weekly.submit_lead_report(self.lead_user, self.project, 'мой отчёт', today=WED)
        new_user, new_lead = self.make_lead('r7-lead2', 'Новый Тимлид')
        member = TeamMember.objects.get(project=self.project, intern=self.lead, status='active')
        staff.replace(member, new_lead, self.head)
        report = LeadWeeklyReport.objects.get()
        self.assertEqual(report.lead, self.lead)
        with self.assertRaises(PermissionDenied):
            weekly.submit_lead_report(self.lead_user, self.project, 'уже не мой', kind='extra', today=WED)


class PmReportTests(Base):
    def test_friday_on_time_other_day_late_and_once(self):
        report = weekly.submit_pm_report(self.pm_user, 'Сводка', project_ids=[self.project.pk, self.second.pk],
                                         today=FRI)
        self.assertTrue(report.on_time)
        self.assertEqual(list(report.projects.all()), [self.project])  # чужой проект не прикрепить
        with self.assertRaises(weekly.ReportError):
            weekly.submit_pm_report(self.pm_user, 'повтор', today=FRI)
        late = weekly.submit_pm_report(self.pm_user, 'за прошлую', week_start=LAST_MON, today=WED)
        self.assertEqual((late.on_time, late.submitted_on), (False, WED))
        with self.assertRaises(PermissionDenied):
            weekly.submit_pm_report(self.lead_user, 'не ПМ', today=FRI)

    def test_metrics_on_time_late_missing(self):
        last = metrics.week_of(LAST_MON, WED)
        current = metrics.week_of(WED, WED)
        r = metrics.pm_reports(current, WED)
        self.assertIsNone(r['missing'])  # до конца субботы пропуска нет
        self.assertEqual(metrics.pm_reports(last, WED)['missing'], 1)
        weekly.submit_pm_report(self.pm_user, 'поздно', week_start=LAST_MON, today=WED)
        r = metrics.pm_reports(last, WED)
        self.assertEqual((r['on_time'], r['other_day'], r['missing']), (0, 1, 0))
        rows = {row['key']: row for row in metrics.compare_weeks(last, WED)['rows']}
        self.assertEqual(rows['pm_late']['current'], 1)
        self.assertIn('lead_missing', rows)


class EvaluationTests(Base):
    def test_scale_rights_and_projects(self):
        archive = InternEvaluation.objects.create(intern=self.intern, hard_skills=4, quality=4, speed=4,
                                                  responsibility=4, communication=4, teamwork=4, independence=4)
        mark, made = weekly.evaluate(self.lead_user, self.intern, self.project, 8, 'стабильно', today=WED)
        self.assertTrue(made)
        self.assertEqual((mark.week_start, mark.lead, mark.score), (MON, self.lead, 8))
        weekly.evaluate(self.lead_user, self.intern, self.second, 6, today=WED)  # отдельно по проекту
        self.assertEqual(WeeklyEvaluation.objects.filter(intern=self.intern).count(), 2)
        _mark, made = weekly.evaluate(self.lead_user, self.intern, self.project, 9, today=WED)
        self.assertFalse(made)
        for bad in (0, 11, 'x'):
            with self.assertRaises(weekly.ReportError):
                weekly.evaluate(self.lead_user, self.intern, self.project, bad, today=WED)
        with self.assertRaises(PermissionDenied):
            weekly.evaluate(self.lead_user, self.front, self.project, 7, today=WED)  # чужое направление
        with self.assertRaises(PermissionDenied):
            weekly.evaluate(self.pm_user, self.intern, self.project, 7, today=WED)  # ПМ не ставит оценки
        archive.refresh_from_db()
        self.assertEqual(archive.average, 4)  # архив 7 критериев не тронут
        self.assertEqual(InternEvaluation.objects.count(), 1)

    def test_other_lead_cannot_override(self):
        weekly.evaluate(self.lead_user, self.intern, self.project, 8, today=WED)
        new_user, new_lead = self.make_lead('r7-lead3', 'Сменщик')
        staff.replace(TeamMember.objects.get(project=self.project, intern=self.lead, status='active'),
                      new_lead, self.head)
        with self.assertRaises(PermissionDenied):
            weekly.evaluate(new_user, self.intern, self.project, 3, today=WED)
        with self.assertRaises(PermissionDenied):
            weekly.evaluate(self.lead_user, self.intern, self.project, 3, today=WED)  # уже не тимлид проекта
        mark = WeeklyEvaluation.objects.get(project=self.project)
        self.assertEqual((mark.lead, mark.score), (self.lead, 8))

    def test_week_is_monday_to_sunday(self):
        self.assertEqual(metrics.week_of(D(2026, 10, 25), WED).start, MON)  # воскресенье
        self.assertEqual(metrics.week_of(D(2026, 10, 26), WED).start, D(2026, 10, 26))


class PagesAndReminderTests(Base):
    def test_portals_and_head_pages(self):
        weekly.submit_lead_report(self.lead_user, self.project, 'Отчёт для ПМ')
        self.client.force_login(self.lead_user)
        page = self.client.get(reverse('lead_portal:weekly_reports'))
        self.assertContains(page, 'Стажёр Бэка')
        self.assertNotContains(page, 'Стажёр Фронта')
        self.assertNotContains(page, 'gp-legacy')
        self.client.post(reverse('lead_portal:weekly_evaluate', args=[self.project.pk]),
                         {'intern': self.intern.pk, 'score': '7', 'comment': 'ок'})
        self.assertTrue(WeeklyEvaluation.objects.filter(score=7, lead=self.lead).exists())
        self.assertNotEqual(self.client.get(reverse('weekly_reports:board')).status_code, 200)
        self.client.force_login(self.pm_user)
        page = self.client.get(reverse('pm_portal:weekly_reports'))
        self.assertContains(page, 'Отчёт для ПМ')
        self.client.post(reverse('pm_portal:weekly_report_submit'), {'text': 'Сводка недели',
                                                                     'projects': [self.project.pk]})
        self.assertEqual(PmWeeklyReport.objects.count(), 1)
        self.client.post(reverse('lead_portal:weekly_evaluate', args=[self.project.pk]),
                         {'intern': self.intern.pk, 'score': '1'})
        self.assertFalse(WeeklyEvaluation.objects.filter(score=1).exists())
        self.client.force_login(self.head)
        board = self.client.get(reverse('weekly_reports:board'), {'week': metrics.last_complete_week().key})
        self.assertContains(board, 'Отсутствуют обязательные отчёты')
        self.assertNotContains(board, 'gp-legacy')
        self.assertContains(self.client.get(reverse('dashboard')), 'Отчёты тимлидов')

    def test_reminders_are_internal_and_deduplicated(self):
        self.assertEqual(weekly.remind(WED), 0)  # не пятница
        created = weekly.remind(FRI)
        self.assertEqual(created, 2)  # тимлид и ПМ
        self.assertEqual(weekly.remind(FRI), 0)
        self.assertEqual(Notification.objects.filter(intern__in=[self.lead, self.pm]).count(), 2)

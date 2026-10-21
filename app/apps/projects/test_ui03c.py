"""UI-03C: заморозка и возвращение по заявке, доступ тимлида к истории,
порталы ПМ и тимлида на дизайн-системе 2.0."""
import datetime
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.dashboard.graduation import compute
from apps.documents import services as doc_services
from apps.interns import freeze
from apps.interns.models import Intern, InternshipRequest, InternStatus
from apps.projects import closure
from apps.projects.fixtures_closure import head, make_ready
from apps.projects.models import Project, ProjectStatus
from apps.projects.services import create_project, set_recruitment
from apps.tasks.models import Task, TaskComment, TaskStatus
from apps.teams.models import TeamMember, TeamRole
from apps.teams.rules import check_assignment
from apps.training.models import Specialization

UserModel = get_user_model()
END = datetime.date(2026, 9, 30)


def portal(name, role, team_role, *projects):
    user = UserModel.objects.create_user(username=name, password='x', role=role)
    intern = Intern.objects.create(full_name=name, user=user)
    for project in projects:
        TeamMember.objects.create(project=project, intern=intern, role=team_role, status=TeamMember.Status.ACTIVE)
    return user, intern


def member(project, intern, role=TeamRole.BACKEND):
    return TeamMember.objects.create(project=project, intern=intern, role=role, status=TeamMember.Status.ACTIVE)


def graduate(name, spec):
    done = Project.objects.create(name=f'Сданный {name}', status=ProjectStatus.COMPLETED, actual_end_date=END)
    person = Intern.objects.create(full_name=name, specialization=spec, status=InternStatus.ACTIVE)
    TeamMember.objects.create(project=done, intern=person, role=TeamRole.BACKEND, status=TeamMember.Status.LEFT,
                              left_reason=TeamMember.LeftReason.PROJECT_ENDED, left_at=END)
    return person


class FreezeFlowTests(TestCase):
    def setUp(self):
        doc_services.ensure_default_types()
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.a = create_project(Project(name='Альфа'))
        self.b = create_project(Project(name='Бета'))
        self.user = UserModel.objects.create_user(username='f-intern', password='x', role=User.Role.INTERN)
        self.intern = Intern.objects.create(full_name='Замораживаемый', specialization=self.backend,
                                            status=InternStatus.ACTIVE, user=self.user, telegram_chat_id=501)
        self.ma, self.mb = member(self.a, self.intern), member(self.b, self.intern)

    def test_request_needs_reason_and_changes_nothing_until_approved(self):
        with self.assertRaises(freeze.FreezeError):
            freeze.request_freeze(self.intern, '  ')
        req = freeze.request_freeze(self.intern, 'Сессия в университете')
        self.assertEqual(req.status, InternshipRequest.Status.PENDING)
        self.ma.refresh_from_db()
        self.intern.refresh_from_db()
        self.assertEqual((self.ma.status, self.intern.status), (TeamMember.Status.ACTIVE, InternStatus.ACTIVE))
        with self.assertRaises(freeze.FreezeError):  # вторая ожидающая заявка — нельзя
            freeze.request_freeze(self.intern, 'ещё раз')

    def test_approval_leaves_all_projects_and_resets_unfinished_tasks(self):
        author = UserModel.objects.create_user(username='f-author', password='x')
        tasks = {status: Task.objects.create(project=self.a, title=f't-{status}', status=status,
                                             assignee=self.user, author=author)
                 for status in ('new', 'in_progress', 'review', 'done')}
        foreign = Task.objects.create(project=self.a, title='чужая', status='in_progress', assignee=author)
        TaskComment.objects.create(task=tasks['in_progress'], author=author, text='Комментарий ревью')
        req = freeze.approve(freeze.request_freeze(self.intern, 'Болезнь'), self.head)

        for m in (self.ma, self.mb):
            m.refresh_from_db()
            self.assertEqual((m.status, m.left_reason), (TeamMember.Status.LEFT, TeamMember.LeftReason.FROZEN))
            self.assertIsNotNone(m.left_at)
        self.assertEqual(TeamMember.objects.filter(intern=self.intern).count(), 2)  # не удалены
        statuses = {k: Task.objects.get(pk=t.pk) for k, t in tasks.items()}
        self.assertEqual(statuses['in_progress'].status, TaskStatus.NEW)
        self.assertEqual(statuses['review'].status, TaskStatus.NEW)
        self.assertEqual(statuses['done'].status, TaskStatus.DONE)
        self.assertEqual(statuses['in_progress'].assignee, self.user)
        self.assertEqual(statuses['in_progress'].author, author)
        self.assertTrue(TaskComment.objects.filter(task=tasks['in_progress']).exists())
        self.assertEqual(Task.objects.get(pk=foreign.pk).status, 'in_progress')
        self.assertEqual(req.tasks_reset, 2)
        self.assertEqual(req.left_projects.split('\n'), ['Альфа', 'Бета'])
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.status, InternStatus.PAUSED)
        self.assertTrue(AuditLog.objects.filter(object_type='Intern', object_id=str(self.intern.pk),
                                                action='Стажировка заморожена', reason='Болезнь').exists())
        self.assertTrue(AuditLog.objects.filter(object_type='Task', action='Возвращена «К выполнению»').exists())

    def test_frees_places_and_counts(self):
        from apps.projects import card

        freeze.freeze_by_head(self.intern, 'Отпуск', self.head)
        self.assertEqual(card.team(self.a)['unique'], 0)
        set_recruitment(self.a, True, self.head)
        self.a.refresh_from_db()
        novice = Intern.objects.create(full_name='На освободившееся', specialization=self.backend)
        self.assertEqual(check_assignment(novice, self.a, TeamRole.BACKEND)[0], [])

    def test_frozen_is_not_graduated_when_former_project_closes(self):
        freeze.freeze_by_head(self.intern, 'Отпуск', self.head)
        make_ready(self.a, self.head)
        result = closure.close_project(self.a, self.head)
        self.assertEqual(result.graduates, [])
        self.assertNotIn(self.intern.pk, compute([self.intern.pk]).ids)

    def test_close_first_then_freeze(self):
        """Закрыли проект раньше одобрения — выпуск состоялся, заморозка закрывает только оставшиеся участия."""
        make_ready(self.a, self.head)
        closure.close_project(self.a, self.head)
        freeze.freeze_by_head(self.intern, 'Отпуск', self.head)
        self.ma.refresh_from_db()
        self.mb.refresh_from_db()
        self.assertEqual(self.ma.left_reason, TeamMember.LeftReason.PROJECT_ENDED)
        self.assertEqual(self.mb.left_reason, TeamMember.LeftReason.FROZEN)

    def test_double_approval_refreeze_and_rights(self):
        req = freeze.request_freeze(self.intern, 'Причина')
        pm, _ = portal('f-pm', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.a)
        with self.assertRaises(PermissionDenied):
            freeze.approve(req, pm)
        freeze.approve(req, self.head)
        with self.assertRaises(freeze.FreezeError):
            freeze.approve(req, self.head)  # повторное подтверждение
        with self.assertRaises(freeze.FreezeError):
            freeze.request_freeze(Intern.objects.get(pk=self.intern.pk), 'снова')  # повторная заморозка
        self.assertEqual(AuditLog.objects.filter(object_id=str(self.intern.pk), action='Стажировка заморожена').count(), 1)

    def test_stale_pending_freeze_of_already_frozen_is_refused(self):
        req = freeze.request_freeze(self.intern, 'Причина')
        Intern.objects.filter(pk=self.intern.pk).update(status=InternStatus.PAUSED)
        with self.assertRaises(freeze.FreezeError):
            freeze.approve(req, self.head)
        req.refresh_from_db()
        self.assertEqual(req.status, InternshipRequest.Status.PENDING)

    def test_reject_changes_nothing(self):
        req = freeze.request_freeze(self.intern, 'Причина')
        freeze.reject(req, self.head, 'Сначала доделайте задачу')
        self.ma.refresh_from_db()
        self.assertEqual(self.ma.status, TeamMember.Status.ACTIVE)
        req.refresh_from_db()
        self.assertEqual(req.status, InternshipRequest.Status.REJECTED)

    def test_intern_is_told_after_commit(self):
        req = freeze.request_freeze(self.intern, 'Причина')
        with mock.patch('apps.graduate_bot.bot.bot.send_message') as send:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                freeze.approve(req, self.head)
            send.assert_not_called()
            # этап 8: уведомление — через очередь (apps.tgbot.outbox), отправка — после коммита
            with self.captureOnCommitCallbacks(execute=True):
                for callback in callbacks:
                    callback()
            send.assert_called_once()
            self.assertIn('/return', send.call_args[0][1])


class ReturnFlowTests(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.old = create_project(Project(name='Прежний', recruitment_open=True))
        self.new = create_project(Project(name='Новый', recruitment_open=True))
        self.closed = create_project(Project(name='Закрытый набор'))
        self.intern = Intern.objects.create(full_name='Возвращенец', specialization=self.backend, status=InternStatus.ACTIVE)
        member(self.old, self.intern)
        freeze.freeze_by_head(self.intern, 'Отпуск', self.head)
        self.intern.refresh_from_db()

    def test_return_needs_frozen_and_reason(self):
        other = Intern.objects.create(full_name='Не замёрз', status=InternStatus.ACTIVE)
        with self.assertRaises(freeze.FreezeError):
            freeze.request_return(other, 'хочу')
        with self.assertRaises(freeze.FreezeError):
            freeze.request_return(self.intern, '')

    def test_approved_return_does_not_restore_old_membership(self):
        freeze.approve(freeze.request_return(self.intern, 'Готов'), self.head)
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.status, InternStatus.WAITING)
        self.assertFalse(TeamMember.objects.filter(intern=self.intern, status=TeamMember.Status.ACTIVE).exists())

    def test_choose_project_respects_rules_and_only_once(self):
        with self.assertRaises(freeze.FreezeError):
            freeze.choose_project(self.intern, self.new)  # возвращение ещё не одобрено
        freeze.approve(freeze.request_return(self.intern, 'Готов'), self.head)
        intern = Intern.objects.get(pk=self.intern.pk)
        offered = freeze.available_projects(intern)
        self.assertIn(self.new, offered)
        self.assertNotIn(self.closed, offered)
        with self.assertRaises(freeze.FreezeError):
            freeze.choose_project(intern, self.closed)  # закрытый набор
        freeze.choose_project(intern, self.new)
        intern.refresh_from_db()
        self.assertEqual(intern.status, InternStatus.ACTIVE)
        self.assertTrue(TeamMember.objects.filter(project=self.new, intern=intern, status='active').exists())
        with self.assertRaises(freeze.FreezeError):
            freeze.choose_project(intern, self.old)  # выбор один раз
        self.assertEqual(freeze.available_projects(intern), [])

    def test_limits_apply(self):
        for i in range(4):
            member(self.new, graduate(f'Старый {i}', self.backend))
        old_intern = graduate('Старый возвращенец', self.backend)
        member(self.old, old_intern)
        freeze.freeze_by_head(old_intern, 'Отпуск', self.head)
        freeze.return_by_head(Intern.objects.get(pk=old_intern.pk), 'Готов', self.head)
        old_intern.refresh_from_db()
        self.assertNotIn(self.new, freeze.available_projects(old_intern))  # 4 старых в направлении
        with self.assertRaises(freeze.FreezeError):
            freeze.choose_project(old_intern, self.new)

    def test_pm_direction_is_assigned_by_head(self):
        pm_spec = Specialization.objects.create(name='PM')
        person = Intern.objects.create(full_name='ПМ-стажёр', specialization=pm_spec, status=InternStatus.ACTIVE)
        freeze.freeze_by_head(person, 'Отпуск', self.head)
        freeze.return_by_head(Intern.objects.get(pk=person.pk), 'Готов', self.head)
        person.refresh_from_db()
        self.assertEqual(freeze.available_projects(person), [])
        with self.assertRaises(freeze.FreezeError):
            freeze.choose_project(person, self.new)


class BotRequestTests(TestCase):
    def setUp(self):
        self.backend = Specialization.objects.create(name='Backend')
        self.intern = Intern.objects.create(full_name='Из бота', specialization=self.backend,
                                            status=InternStatus.ACTIVE, telegram_chat_id=777)

    def message(self, text):
        return SimpleNamespace(text=text, chat=SimpleNamespace(id=777))

    def test_freeze_and_return_requests_from_bot(self):
        from apps.graduate_bot import bot as gbot

        with mock.patch.object(gbot.bot, 'send_message') as send:
            gbot.handle_request_reason(self.message('Экзамены'), self.intern.pk, 'freeze')
            self.assertIn('отправлена', send.call_args[0][1])
            gbot.handle_request_reason(self.message('Отмена'), self.intern.pk, 'freeze')
        req = InternshipRequest.objects.get(intern=self.intern)
        self.assertEqual((req.kind, req.reason, req.source), ('freeze', 'Экзамены', 'bot'))

    def test_projects_command_lists_only_allowed(self):
        from apps.graduate_bot import bot as gbot

        head_user = head()
        open_project = create_project(Project(name='Открытый', recruitment_open=True))
        create_project(Project(name='Без набора'))
        freeze.freeze_by_head(self.intern, 'Отпуск', head_user)
        freeze.return_by_head(Intern.objects.get(pk=self.intern.pk), 'Готов', head_user)
        with mock.patch.object(gbot.bot, 'send_message') as send, \
                mock.patch.object(gbot.bot, 'register_next_step_handler'):
            gbot.handle_projects(self.message('/projects'))
            markup = send.call_args[1]['reply_markup']
            buttons = [b['text'] for row in markup.keyboard for b in row]
            self.assertIn('Открытый', buttons)
            self.assertNotIn('Без набора', buttons)
            gbot.handle_project_choice(self.message('Открытый'), self.intern.pk)
        self.assertTrue(TeamMember.objects.filter(project=open_project, intern=self.intern, status='active').exists())


class ReconciliationTests(TestCase):
    def test_dry_run_then_apply_is_idempotent(self):
        user = UserModel.objects.create_user(username='r-intern', password='x')
        project = create_project(Project(name='Старая заморозка'))
        intern = Intern.objects.create(full_name='Старый замороженный', status=InternStatus.PAUSED, user=user)
        membership = member(project, intern)
        task = Task.objects.create(project=project, title='висит', status='in_progress', assignee=user)
        out = StringIO()
        call_command('freeze_reconciliation', stdout=out)
        self.assertIn('Старый замороженный', out.getvalue())
        membership.refresh_from_db()
        self.assertEqual(membership.status, TeamMember.Status.ACTIVE)  # отчёт ничего не меняет
        call_command('freeze_reconciliation', '--apply', stdout=StringIO())
        membership.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual((membership.status, membership.left_reason), ('left', TeamMember.LeftReason.FROZEN))
        self.assertEqual(task.status, TaskStatus.NEW)
        out = StringIO()
        call_command('freeze_reconciliation', '--apply', stdout=out)
        self.assertIn('Расхождений нет', out.getvalue())


class HeadFreezeUiTests(TestCase):
    def setUp(self):
        self.head = head()
        self.intern = Intern.objects.create(full_name='Заявитель', status=InternStatus.ACTIVE)
        self.client.force_login(self.head)

    def test_requests_page_and_decisions(self):
        req = freeze.request_freeze(self.intern, 'Переезд')
        page = self.client.get(reverse('interns:internship_requests'))
        self.assertContains(page, 'Переезд')
        self.assertContains(page, f'req-approve-{req.pk}')
        self.assertNotContains(page, 'gp-legacy')
        self.client.post(reverse('interns:internship_request_decide', args=[req.pk, 'approve']))
        req.refresh_from_db()
        self.assertEqual(req.status, InternshipRequest.Status.APPROVED)
        self.assertContains(self.client.get(reverse('interns:internship_requests')), 'Одобрена')

    def test_pm_cannot_open_requests(self):
        pm, _ = portal('h-pm', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER)
        self.client.force_login(pm)
        response = self.client.get(reverse('interns:internship_requests'))
        self.assertNotEqual(response.status_code, 200)


class LeadHistoryTests(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.project = create_project(Project(name='История'))
        self.lead_user, self.lead = portal('l-lead', User.Role.TEAM_LEAD, TeamRole.TEAM_LEAD, self.project)
        self.lead.specialization = self.backend
        self.lead.save()
        self.intern = Intern.objects.create(full_name='Бывший Бэкендер', specialization=self.backend,
                                            status=InternStatus.ACTIVE)
        member(self.project, self.intern)
        freeze.freeze_by_head(self.intern, 'Отпуск', self.head)
        self.client.force_login(self.lead_user)
        self.detail = reverse('lead_portal:intern_detail', args=[self.project.pk, self.intern.pk])

    def test_lead_sees_history_of_frozen_member(self):
        response = self.client.get(self.detail)
        self.assertContains(response, 'Бывший Бэкендер')
        self.assertContains(response, 'стажировка заморожена')
        self.assertContains(response, 'Вышел(а) из команды')

    def test_former_lead_gets_no_new_rights(self):
        TeamMember.objects.filter(intern=self.lead).update(status=TeamMember.Status.LEFT)
        make_ready(self.project, self.head)
        closure.close_project(self.project, self.head)
        self.assertEqual(self.client.get(self.detail).status_code, 200)  # история — видна
        frozen_member = TeamMember.objects.get(intern=self.intern)
        for url, data in (
            (reverse('lead_portal:member_edit', args=[self.project.pk, frozen_member.pk]), {'status': 'active'}),
            (reverse('lead_portal:member_delete', args=[self.project.pk, frozen_member.pk]), {}),
            (reverse('lead_portal:intern_to_reserve', args=[self.project.pk, self.intern.pk]), {'comment': 'x'}),
            (reverse('lead_portal:profile_link_create', args=[self.project.pk]), {'ttl_days': '7'}),
            (reverse('lead_portal:task_create', args=[self.project.pk]), {'title': 'взлом'}),
        ):
            with self.subTest(url=url):
                self.assertIn(self.client.post(url, data).status_code, (302, 404))
        self.assertFalse(Task.objects.filter(title='взлом').exists())
        frozen_member.refresh_from_db()
        self.assertEqual(frozen_member.status, TeamMember.Status.LEFT)


# ======================================================================= порталы

class PortalCardTests(TestCase):
    def setUp(self):
        doc_services.ensure_default_types()
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.project = create_project(Project(name='Портальный'))
        self.other = create_project(Project(name='Чужой портальный'))
        self.pm_user, _ = portal('p-pm', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.project)
        self.lead_user, lead = portal('p-lead', User.Role.TEAM_LEAD, TeamRole.TEAM_LEAD, self.project)
        lead.specialization = self.backend
        lead.save()
        member(self.project, Intern.objects.create(full_name='Стажёр Портала', specialization=self.backend))

    def test_pm_tabs_render_in_new_ui(self):
        self.client.force_login(self.pm_user)
        base = reverse('pm_portal:project_detail', args=[self.project.pk])
        for tab in ('overview', 'stages', 'team', 'tasks', 'documents', 'report', 'attendance', 'client'):
            with self.subTest(tab=tab):
                response = self.client.get(base, {'tab': tab})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'gp-tabs')
                self.assertNotContains(response, 'gp-legacy')
                self.assertNotContains(response, 'Завершить проект')
                self.assertNotContains(response, reverse('projects:recruitment_set', args=[self.project.pk]))
        self.assertContains(self.client.get(base, {'tab': 'stages'}), 'Сохранить основной этап')
        self.assertEqual(self.client.get(reverse('pm_portal:project_detail', args=[self.other.pk])).status_code, 404)

    def test_lead_tabs_render_in_new_ui(self):
        self.client.force_login(self.lead_user)
        base = reverse('lead_portal:project_detail', args=[self.project.pk])
        for tab in ('overview', 'team', 'tasks', 'attendance', 'documents'):
            with self.subTest(tab=tab):
                response = self.client.get(base, {'tab': tab})
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'gp-legacy')
        response = self.client.get(base, {'tab': 'report'})
        self.assertNotContains(response, 'report_create')  # отчётов у тимлида нет
        self.assertContains(self.client.get(base, {'tab': 'team'}), 'Стажёр Портала')

    def test_tasks_for_pm_and_lead(self):
        self.client.force_login(self.pm_user)
        self.client.post(reverse('pm_portal:task_create', args=[self.project.pk]),
                         {'title': 'Сверстать главную', 'priority': 'high', 'deadline': '2026-11-01'})
        task = Task.objects.get(title='Сверстать главную')
        self.assertEqual((task.author, task.priority), (self.pm_user, 'high'))
        self.client.post(reverse('pm_portal:task_status', args=[self.project.pk, task.pk]), {'status': 'in_progress'})
        task.refresh_from_db()
        self.assertEqual(task.status, 'in_progress')
        self.client.force_login(self.lead_user)
        self.client.post(reverse('lead_portal:task_status', args=[self.project.pk, task.pk]), {'status': 'review'})
        task.refresh_from_db()
        self.assertEqual(task.status, 'review')
        self.client.post(reverse('lead_portal:task_delete', args=[self.project.pk, task.pk]))
        task.refresh_from_db()
        self.assertFalse(task.is_archived)  # удаляют руководитель и ПМ
        foreign = Task.objects.create(project=self.other, title='чужая')
        self.client.force_login(self.pm_user)
        response = self.client.post(reverse('pm_portal:task_status', args=[self.project.pk, foreign.pk]), {'status': 'done'})
        self.assertEqual(response.status_code, 404)

    def test_completed_project_tasks_are_read_only_for_portals(self):
        task = Task.objects.create(project=self.project, title='Финальная', status='new')
        make_ready(self.project, self.head)
        closure.close_project(self.project, self.head)
        self.client.force_login(self.lead_user)
        self.client.post(reverse('lead_portal:task_status', args=[self.project.pk, task.pk]), {'status': 'done'})
        task.refresh_from_db()
        self.assertEqual(task.status, 'new')

    def test_head_card_uses_new_tasks_and_reports(self):
        self.client.force_login(self.head)
        for tab in ('tasks', 'report'):
            response = self.client.get(self.project.get_absolute_url(), {'tab': tab})
            self.assertNotContains(response, 'gp-legacy')
        self.client.post(reverse('projects:task_create', args=[self.project.pk]), {'title': 'От руководителя'})
        self.assertTrue(Task.objects.filter(title='От руководителя', project=self.project).exists())

    def test_meeting_sheet_pm_read_only_lead_editable(self):
        from apps.attendance import services as attendance_services
        from apps.attendance.models import MeetingKind

        group = self.project.group
        TeamMember.objects.filter(project=self.project).update(group=group)  # участники группы потока
        meeting = attendance_services.create_meeting(group, kind=MeetingKind.INTERNAL, date=datetime.date(2026, 10, 1))
        self.client.force_login(self.pm_user)
        response = self.client.get(reverse('pm_portal:meeting_detail', args=[self.project.pk, meeting.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'hx-post')
        self.assertNotContains(response, 'gp-legacy')
        self.client.force_login(self.lead_user)
        response = self.client.get(reverse('lead_portal:meeting_detail', args=[self.project.pk, meeting.pk]))
        self.assertContains(response, 'hx-post')
        self.assertNotContains(response, 'gp-legacy')

    def test_member_form_and_intern_card_in_new_ui(self):
        self.client.force_login(self.pm_user)
        response = self.client.get(reverse('pm_portal:member_add', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'gp-legacy')
        intern = Intern.objects.get(full_name='Стажёр Портала')
        response = self.client.get(reverse('pm_portal:intern_detail', args=[self.project.pk, intern.pk]))
        self.assertContains(response, 'Участие в проекте')
        self.assertNotContains(response, 'gp-legacy')

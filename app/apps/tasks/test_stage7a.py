"""Этап 7A: четыре состояния, Kanban, исполнители, сдача со скриншотом и проверка."""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.interns import discipline, freeze
from apps.interns.models import Intern, InternStatus
from apps.projects import closure
from apps.projects.fixtures_closure import head, make_ready
from apps.projects.models import Project
from apps.projects.services import create_project
from apps.tasks import workflow
from apps.tasks.models import BOARD_STATUSES, Task, TaskAssignment, TaskEvent, TaskStatus, TaskSubmission
from apps.teams import staff
from apps.teams.models import TeamMember, TeamRole
from apps.teams.services import leave_team
from apps.training.models import Specialization

UserModel = get_user_model()


def shot(name='result.png'):
    return SimpleUploadedFile(name, b'\x89PNG\r\n\x1a\nfake', content_type='image/png')


class Base(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.frontend = Specialization.objects.create(name='Frontend')
        self.project = create_project(Project(name='Канбан', recruitment_open=True))
        self.other = create_project(Project(name='Чужой', recruitment_open=True))
        self.pm_user = UserModel.objects.create_user(username='k-pm', password='x', role=User.Role.PROJECT_MANAGER)
        pm = Intern.objects.create(full_name='ПМ Проекта', user=self.pm_user)
        TeamMember.objects.create(project=self.project, intern=pm, role=TeamRole.PROJECT_MANAGER, status='active')
        self.lead_user = UserModel.objects.create_user(username='k-lead', password='x', role=User.Role.TEAM_LEAD)
        self.lead = Intern.objects.create(full_name='Тимлид Бэка', user=self.lead_user, specialization=self.backend,
                                          position='lead', staff_role='lead', staff_status='active')
        staff.assign_lead(self.project, self.lead, self.head)
        self.a = self.member('Стажёр А', self.backend, TeamRole.BACKEND, chat=111)
        self.b = self.member('Стажёр Б', self.backend, TeamRole.BACKEND)
        self.f = self.member('Фронт В', self.frontend, TeamRole.FRONTEND)

    def member(self, name, spec, role, chat=None, project=None):
        intern = Intern.objects.create(full_name=name, specialization=spec, status=InternStatus.ACTIVE,
                                       telegram_chat_id=chat)
        TeamMember.objects.create(project=project or self.project, intern=intern, role=role, status='active')
        return intern

    def task(self, **kw):
        kw.setdefault('title', 'Сделать API')
        kw.setdefault('direction', 'backend')
        return workflow.create_task(self.project, self.head, **kw)


class StatusesAndRightsTests(Base):
    def test_four_board_states_and_legacy_mapping(self):
        self.assertEqual([TaskStatus(s).label for s in BOARD_STATUSES],
                         ['К выполнению', 'В работе', 'На проверке', 'Готово'])
        self.assertEqual(TaskStatus.NEW, 'new')  # хранимые значения прежние
        Task.objects.create(project=self.project, title='Старая отменённая', status='cancelled')
        self.client.force_login(self.head)
        response = self.client.get(self.project.get_absolute_url(), {'tab': 'tasks'})
        self.assertEqual([c['key'] for c in response.context['task_columns']], list(BOARD_STATUSES))
        self.assertNotContains(response, 'Старая отменённая')
        self.assertNotContains(response, 'gp-legacy')

    def test_manual_transitions(self):
        task = self.task(direction='')
        with self.assertRaises(workflow.TaskError):
            workflow.set_status(task, TaskStatus.DONE, self.head)  # прыжок через состояния
        self.assertTrue(workflow.set_status(task, TaskStatus.IN_PROGRESS, self.head))
        self.assertFalse(workflow.set_status(task, TaskStatus.IN_PROGRESS, self.head))  # повтор
        workflow.assign(task, self.a, self.head)
        task.refresh_from_db()
        for status in (TaskStatus.REVIEW, TaskStatus.DONE):
            with self.assertRaises(workflow.TaskError):
                workflow.set_status(task, status, self.head)  # только сдачей и проверкой
        with self.assertRaises(workflow.TaskError):
            workflow.set_status(task, 'cancelled', self.head)

    def test_lead_direction_and_members_only(self):
        task = workflow.create_task(self.project, self.lead_user, title='Лидская', direction='frontend')
        self.assertEqual(task.direction, 'backend')  # тимлид ставит только своему направлению
        with self.assertRaises(workflow.TaskError):
            workflow.assign(task, self.f, self.lead_user)  # чужое направление
        outsider = Intern.objects.create(full_name='Не участник', specialization=self.backend)
        with self.assertRaises(workflow.TaskError):
            workflow.assign(task, outsider, self.lead_user)
        front = workflow.create_task(self.project, self.pm_user, title='Фронт', direction='frontend')
        with self.assertRaises(PermissionDenied):
            workflow.set_status(front, TaskStatus.IN_PROGRESS, self.lead_user)
        with self.assertRaises(PermissionDenied):
            workflow.assign(front, self.f, self.lead_user)
        workflow.assign(front, self.f, self.pm_user)  # ПМ — все задачи своего проекта
        foreign = Task.objects.create(project=self.other, title='Чужая')
        with self.assertRaises(PermissionDenied):
            workflow.set_status(foreign, TaskStatus.IN_PROGRESS, self.pm_user)
        self.client.force_login(self.pm_user)
        self.assertEqual(self.client.get(reverse('pm_portal:task_detail', args=[self.other.pk, foreign.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('pm_portal:task_status', args=[self.project.pk, foreign.pk]),
                                          {'status': 'in_progress'}).status_code, 404)
        self.client.force_login(self.lead_user)
        self.client.post(reverse('lead_portal:task_delete', args=[self.project.pk, task.pk]))
        task.refresh_from_db()
        self.assertFalse(task.is_archived)  # архивируют руководитель и ПМ

    def test_frozen_intern_gets_no_new_tasks(self):
        freeze.freeze_by_head(self.a, 'болезнь', self.head)
        with self.assertRaises(workflow.TaskError):
            workflow.assign(self.task(), self.a, self.head)


class SubmissionAndReviewTests(Base):
    def test_multiple_assignees_need_every_approval(self):
        task = self.task()
        workflow.assign(task, self.a, self.lead_user)
        workflow.assign(task, self.b, self.lead_user)
        self.assertEqual(workflow.assign(task, self.a, self.lead_user)[1], False)  # без дубля
        sub_a = workflow.submit_from_telegram(111, task.pk, screenshot=shot(), links='https://example.com/pr/1',
                                              comment='готово')
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)  # Б ещё не сдал
        workflow.review(sub_a, self.lead_user, approve=True)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        sub_b = workflow.submit_work(task.pk, self.b, screenshot=shot())
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.REVIEW)
        workflow.review(sub_b, self.lead_user, approve=True)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertIsNotNone(task.completed_at)
        a = TaskAssignment.objects.get(task=task, intern=self.a)
        self.assertEqual((a.status, a.approved_by), (TaskStatus.DONE, self.lead_user))
        self.assertIsNotNone(a.approved_at)

    def test_screenshot_required_and_validated(self):
        task = self.task()
        workflow.assign(task, self.a, self.head)
        for bad in (None, SimpleUploadedFile('x.exe', b'MZ'), SimpleUploadedFile('empty.png', b'')):
            with self.assertRaises(workflow.TaskError):
                workflow.submit_work(task.pk, self.a, screenshot=bad)
        with self.assertRaises(workflow.TaskError):
            workflow.submit_work(task.pk, self.a, screenshot=shot(), links='javascript:alert(1)')
        with self.assertRaises(workflow.TaskError):
            workflow.submit_work(task.pk, self.b, screenshot=shot())  # не исполнитель
        with self.assertRaises(workflow.TaskError):
            workflow.submit_from_telegram(999, task.pk, screenshot=shot())  # чужой Telegram
        self.assertFalse(TaskSubmission.objects.exists())

    def test_return_keeps_attempts_and_repeat_is_refused(self):
        task = self.task()
        workflow.assign(task, self.a, self.head)
        first = workflow.submit_work(task.pk, self.a, screenshot=shot())
        with self.assertRaises(workflow.TaskError):
            workflow.submit_work(task.pk, self.a, screenshot=shot())  # уже на проверке
        with self.assertRaises(workflow.TaskError):
            workflow.review(first, self.lead_user, approve=False)  # без причины
        with self.assertRaises(PermissionDenied):
            workflow.review(first, UserModel.objects.create_user(username='k-x', password='x', role='team_lead'),
                            approve=True)
        workflow.review(first, self.lead_user, approve=False, comment='Нет валидации')
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        with self.assertRaises(workflow.TaskError):
            workflow.review(first, self.lead_user, approve=True)  # повторная проверка
        second = workflow.submit_work(task.pk, self.a, screenshot=shot('v2.png'))
        self.assertEqual(second.attempt, 2)
        first.refresh_from_db()
        self.assertEqual(first.outcome, TaskSubmission.Outcome.RETURNED)
        self.assertTrue(first.screenshot.name)
        kinds = set(task.events.values_list('kind', flat=True))
        self.assertTrue({'created', 'assigned', 'submitted', 'returned', 'status'} <= kinds)

    def test_db_guards_against_duplicates(self):
        task = self.task()
        assignment, _ = workflow.assign(task, self.a, self.head)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TaskAssignment.objects.create(task=task, intern=self.a, person_name='дубль')
        workflow.submit_work(task.pk, self.a, screenshot=shot())
        with self.assertRaises(IntegrityError), transaction.atomic():
            TaskSubmission.objects.create(task=task, assignment=assignment, screenshot=shot())

    def test_closed_project_is_read_only(self):
        task = self.task()
        workflow.assign(task, self.a, self.head)
        make_ready(self.project, self.head)
        closure.close_project(self.project, self.head)
        task.refresh_from_db()
        with self.assertRaises(workflow.TaskError):
            workflow.submit_work(task.pk, self.a, screenshot=shot())
        self.client.force_login(self.lead_user)
        self.client.post(reverse('lead_portal:task_status', args=[self.project.pk, task.pk]), {'status': 'in_progress'})
        page = self.client.get(reverse('lead_portal:task_detail', args=[self.project.pk, task.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'task-edit-form')

    def test_pages_and_review_through_views(self):
        task = self.task()
        workflow.assign(task, self.a, self.head)
        sub = workflow.submit_work(task.pk, self.a, screenshot=shot())
        for user, name in ((self.head, 'projects'), (self.pm_user, 'pm_portal'), (self.lead_user, 'lead_portal')):
            self.client.force_login(user)
            detail = reverse(f'{name}:task_detail', args=[self.project.pk, task.pk])
            board = self.client.get(reverse('projects:detail' if name == 'projects' else f'{name}:project_detail',
                                            args=[self.project.pk]), {'tab': 'tasks', 'direction': 'backend'})
            self.assertContains(board, detail)
            page = self.client.get(detail)
            self.assertContains(page, 'Принять')
            self.assertNotContains(page, 'gp-legacy')
        self.client.post(reverse('lead_portal:task_review', args=[self.project.pk, task.pk, sub.pk]),
                         {'decision': 'approve'})
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)


class StaffTransitionTests(Base):
    def setUp(self):
        super().setUp()
        self.t = self.task()
        workflow.assign(self.t, self.a, self.head)
        workflow.assign(self.t, self.b, self.head)
        workflow.review(workflow.submit_work(self.t.pk, self.a, screenshot=shot()), self.head, approve=True)
        self.pending = workflow.submit_work(self.t.pk, self.b, screenshot=shot())

    def check_released(self, reason_word):
        self.t.refresh_from_db()
        b = TaskAssignment.objects.get(task=self.t, intern=self.b)
        a = TaskAssignment.objects.get(task=self.t, intern=self.a)
        self.assertFalse(b.is_active)
        self.assertIn(reason_word, b.release_reason)
        self.assertEqual((a.is_active, a.status), (True, TaskStatus.DONE))  # принятое остаётся
        self.assertTrue(self.t.needs_reassign)
        self.assertEqual(self.t.status, TaskStatus.NEW)  # в очереди, а не «Готово»
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.outcome, TaskSubmission.Outcome.WITHDRAWN)
        self.assertTrue(self.t.events.filter(kind=TaskEvent.Kind.RELEASED).exists())
        self.assertFalse(TaskAssignment.objects.filter(intern=self.b, is_active=True).exclude(status='done').exists())

    def test_freeze(self):
        freeze.freeze_by_head(self.b, 'семья', self.head)
        self.check_released('замор')

    def test_expel(self):
        discipline.expel(self.b, self.head, 'нарушения')
        self.check_released('отчисл')

    def test_promotion(self):
        staff.promote_to_lead(self.b, self.head)
        self.check_released('повыш')

    def test_leave_project_and_reassign(self):
        member = TeamMember.objects.get(project=self.project, intern=self.b, status='active')
        leave_team(member)
        self.check_released('')
        c = self.member('Стажёр Г', self.backend, TeamRole.BACKEND)
        workflow.assign(self.t, c, self.head)
        self.t.refresh_from_db()
        self.assertFalse(self.t.needs_reassign)
        self.assertEqual(self.t.status, TaskStatus.IN_PROGRESS)

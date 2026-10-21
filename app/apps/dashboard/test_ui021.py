"""UI-02.1: выпускник по достоверным событиям, ручной основной этап, права."""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.dashboard import graduation, metrics
from apps.interns.models import Intern, InternStatus
from apps.interns.services import pause_person, promote_to_lead, demote_from_lead, unpause_person
from apps.projects import services as project_services
from apps.projects.models import Project, ProjectStage, ProjectStageKey, ProjectStatus, ProjectStatusHistory
from apps.teams.models import TeamMember, TeamRole

UserModel = get_user_model()
D = datetime.date
END = D(2026, 9, 30)


def at(day):
    return mock.patch('django.utils.timezone.now', return_value=datetime.datetime(day.year, day.month, day.day, 9, tzinfo=datetime.timezone.utc))


class GraduationRulesTests(TestCase):
    def setUp(self):
        self.done = Project.objects.create(name='Закрытый', status=ProjectStatus.COMPLETED, actual_end_date=END)

    def closed(self, intern, **kw):
        """Участие, закрытое при официальном завершении (как release_team)."""
        kw.setdefault('left_reason', TeamMember.LeftReason.PROJECT_ENDED)
        kw.setdefault('left_at', END)
        return TeamMember.objects.create(project=self.done, intern=intern, role=TeamRole.BACKEND,
                                         status=TeamMember.Status.LEFT, **kw)

    def test_active_at_official_completion_is_graduate(self):
        person = Intern.objects.create(full_name='Дошёл До Конца')
        self.closed(person)
        result = graduation.compute()
        self.assertIn(person.pk, result.ids)
        self.assertEqual(result.first_dates[person.pk], END)

    def test_readmitted_after_expulsion_is_new_intern(self):
        person = Intern.objects.create(full_name='Вернулся После Отчисления', internship_attempt=2, status=InternStatus.ACTIVE)
        self.closed(person, left_reason=TeamMember.LeftReason.NOT_FIT, left_at=D(2026, 8, 1))
        TeamMember.objects.create(project=Project.objects.create(name='Новый'), intern=person, role=TeamRole.BACKEND)
        result = graduation.compute()
        self.assertNotIn(person.pk, result.ids)
        self.assertIn(person.pk, {r['intern_id'] for r in result.review})
        kpi = metrics.people_kpi()
        self.assertEqual((kpi['new'], kpi['old']), (1, 0))

    def test_graduate_on_two_projects_once(self):
        person = Intern.objects.create(full_name='Выпускник Двух', status=InternStatus.ACTIVE)
        self.closed(person)
        for name in ('А', 'Б'):
            TeamMember.objects.create(project=Project.objects.create(name=name), intern=person, role=TeamRole.BACKEND)
        kpi = metrics.people_kpi()
        self.assertEqual((kpi['active'], kpi['old'], kpi['graduates'], kpi['active_participations']), (1, 1, 1, 2))

    def test_frozen_before_closure_is_not_graduate(self):
        person = Intern.objects.create(full_name='Заморожен До', status=InternStatus.ACTIVE)
        with at(D(2026, 9, 10)):
            pause_person(person)
        self.closed(person)
        self.assertNotIn(person.pk, graduation.compute().ids)

    def test_frozen_and_returned_before_closure_is_graduate(self):
        person = Intern.objects.create(full_name='Вернулся Из Заморозки', status=InternStatus.ACTIVE)
        with at(D(2026, 9, 1)):
            pause_person(person)
        with at(D(2026, 9, 15)):
            unpause_person(Intern.objects.get(pk=person.pk))
        self.closed(person)
        self.assertIn(person.pk, graduation.compute().ids)

    def test_frozen_after_closure_stays_graduate(self):
        person = Intern.objects.create(full_name='Заморожен После', status=InternStatus.ACTIVE)
        self.closed(person)
        with at(D(2026, 10, 10)):
            pause_person(person)
        self.assertIn(person.pk, graduation.compute().ids)

    def test_paused_without_history_goes_to_review(self):
        person = Intern.objects.create(full_name='Пауза Без Журнала', status=InternStatus.PAUSED)
        self.closed(person)
        result = graduation.compute()
        self.assertNotIn(person.pk, result.ids)
        self.assertIn('дата заморозки неизвестна', {r['reason'] for r in result.review if r['intern_id'] == person.pk}.pop())

    def test_left_before_closure_is_not_graduate(self):
        early = Intern.objects.create(full_name='Снят Раньше')
        self.closed(early, left_at=D(2026, 9, 1))  # «Проект завершён», но до даты закрытия
        quitter = Intern.objects.create(full_name='Сам Ушёл')
        self.closed(quitter, left_reason=TeamMember.LeftReason.SELF, left_at=D(2026, 9, 1))
        result = graduation.compute()
        self.assertNotIn(early.pk, result.ids)
        self.assertNotIn(quitter.pk, result.ids)
        self.assertIn(early.pk, {r['intern_id'] for r in result.review})

    def test_promotion_to_lead_is_graduation_and_survives_demotion(self):
        person = Intern.objects.create(full_name='Повышен', status=InternStatus.ACTIVE)
        with at(D(2026, 10, 14)):
            promote_to_lead(person)
        demote_from_lead(Intern.objects.get(pk=person.pk))
        result = graduation.compute()
        self.assertIn(person.pk, result.ids)
        self.assertEqual(result.first_dates[person.pk], D(2026, 10, 14))

    def test_reset_graduate_status_does_not_lose_graduation(self):
        person = Intern.objects.create(full_name='Сброшенная Отметка', graduate_status='')
        self.closed(person)
        self.assertIn(person.pk, graduation.compute().ids)

    def test_reconciliation_command_is_read_only(self):
        from io import StringIO

        from django.core.management import call_command

        Intern.objects.create(full_name='Повторно', internship_attempt=2)
        before = list(Intern.objects.values_list('pk', 'graduate_status', 'status'))
        out = StringIO()
        call_command('graduate_reconciliation', stdout=out)
        self.assertIn('Требуют сверки: 1', out.getvalue())
        self.assertEqual(before, list(Intern.objects.values_list('pk', 'graduate_status', 'status')))


class PrimaryStageTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Проект', current_stage=ProjectStageKey.DESIGN)
        self.backend = ProjectStage.objects.create(project=self.project, key=ProjectStageKey.BACKEND, status=ProjectStage.Status.IN_PROGRESS)
        self.design = ProjectStage.objects.create(project=self.project, key=ProjectStageKey.DESIGN, status=ProjectStage.Status.IN_PROGRESS)
        self.other = Project.objects.create(name='Чужой')
        self.other_stage = ProjectStage.objects.create(project=self.other, key=ProjectStageKey.TESTING)
        self.head = UserModel.objects.create_user(username='ps-head', password='x', role=User.Role.HEAD)

    def portal_user(self, username, role, team_role, project=None):
        user = UserModel.objects.create_user(username=username, password='x', role=role)
        card = Intern.objects.create(full_name=username, user=user)
        if project:
            TeamMember.objects.create(project=project, intern=card, role=team_role, status=TeamMember.Status.ACTIVE)
        return user

    def test_head_sets_primary_stage_from_project_card(self):
        self.client.force_login(self.head)
        response = self.client.post(reverse('projects:primary_stage_set', args=[self.project.pk]), {'stage': self.backend.pk})
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual((self.project.primary_stage, self.project.primary_stage_set_by), (self.backend, self.head))
        self.assertIsNotNone(self.project.primary_stage_set_at)
        self.assertTrue(ProjectStatusHistory.objects.filter(project=self.project, field='Основной этап', new_value='Backend').exists())
        self.assertContains(self.client.get(self.project.get_absolute_url() + '?tab=stages'), 'Выбран: Backend')

    def test_pm_of_project_sets_primary_stage(self):
        pm = self.portal_user('ps-pm', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.project)
        self.client.force_login(pm)
        self.assertContains(self.client.get(reverse('pm_portal:project_detail', args=[self.project.pk]) + '?tab=stages'), 'Основной этап')
        self.client.post(reverse('pm_portal:primary_stage_set', args=[self.project.pk]), {'stage': self.design.pk})
        self.project.refresh_from_db()
        self.assertEqual(self.project.primary_stage, self.design)

    def test_foreign_pm_and_lead_cannot_change(self):
        foreign_pm = self.portal_user('ps-pm2', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.other)
        self.client.force_login(foreign_pm)
        self.assertEqual(self.client.post(reverse('pm_portal:primary_stage_set', args=[self.project.pk]), {'stage': self.backend.pk}).status_code, 404)
        lead = self.portal_user('ps-lead', User.Role.TEAM_LEAD, TeamRole.TEAM_LEAD, self.project)
        self.client.force_login(lead)
        for url in (reverse('projects:primary_stage_set', args=[self.project.pk]), reverse('pm_portal:primary_stage_set', args=[self.project.pk])):
            self.assertEqual(self.client.post(url, {'stage': self.backend.pk}).status_code, 302)  # middleware уводит в портал
        self.assertFalse(project_services.can_set_primary_stage(lead, self.project))
        with self.assertRaises(PermissionDenied):
            project_services.set_primary_stage(self.project, self.backend, lead)
        self.project.refresh_from_db()
        self.assertIsNone(self.project.primary_stage)

    def test_stage_of_another_project_is_rejected(self):
        self.client.force_login(self.head)
        self.assertEqual(self.client.post(reverse('projects:primary_stage_set', args=[self.project.pk]), {'stage': self.other_stage.pk}).status_code, 404)
        with self.assertRaises(ValidationError):
            project_services.set_primary_stage(self.project, self.other_stage, self.head)
        self.project.primary_stage = self.other_stage
        with self.assertRaises(ValidationError):
            self.project.save()  # проверка и на прямом сохранении

    def test_get_is_not_allowed(self):
        self.client.force_login(self.head)
        self.assertEqual(self.client.get(reverse('projects:primary_stage_set', args=[self.project.pk])).status_code, 405)

    def test_stage_changes_do_not_switch_primary_stage(self):
        project_services.set_primary_stage(self.project, self.design, self.head)
        project_services.complete_stage(self.design, user=self.head)  # current_stage уходит сам
        self.project.refresh_from_db()
        self.assertEqual(self.project.primary_stage, self.design)  # основной — нет

    def test_completed_project_counted_as_completed_regardless_of_stage(self):
        project_services.set_primary_stage(self.project, self.backend, self.head)
        Project.objects.filter(pk=self.project.pk).update(status=ProjectStatus.COMPLETED)
        counts = {c['key']: c['count'] for c in metrics.projects_by_stage()['categories']}
        self.assertEqual(counts['completed'], 1)
        self.assertEqual(counts['development'], 0)


class MeetingObligationTests(TestCase):
    def test_obligation_exists_without_meeting_records(self):
        project = Project.objects.create(name='Проект')
        for name, role in (('Б1', TeamRole.BACKEND), ('Б2', TeamRole.BACKEND), ('Ф1', TeamRole.FRONTEND)):
            TeamMember.objects.create(project=project, intern=Intern.objects.create(full_name=name), role=role)
        week = metrics.Week(D(2026, 10, 12), D(2026, 10, 18), True)
        meetings = metrics.weekly_activity(week, D(2026, 10, 21))['meetings']
        self.assertEqual(meetings['obligations'], 2)  # направления, а не люди
        # этап 9B: считается по-настоящему; без тимлида — кадровая проблема, не «пропуск»
        self.assertEqual((meetings['obligations_missed'], meetings['video']['no_lead']), (0, 2))
        self.assertEqual(meetings['held'], 0)

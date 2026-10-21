"""Сдача проекта в 2.0 (UI-03B): закрытие только руководителем и только
через apps.projects.closure — принудительного закрытия больше нет."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.documents import services as doc_services
from apps.projects import closure, delivery
from apps.projects.fixtures_closure import head, make_ready
from apps.projects.models import Project, ProjectStageKey, ProjectStatus
from apps.projects.services import create_project
from apps.teams.models import TeamMember, TeamRole

User = get_user_model()


class DeliveryTests(TestCase):
    def setUp(self):
        doc_services.ensure_default_types()
        self.head = head()
        self.project = create_project(Project(name='Готовый'))

    def test_cannot_complete_without_documents(self):
        with self.assertRaises(closure.ClosureError) as ctx:
            closure.close_project(self.project, self.head)
        text = ' '.join(ctx.exception.messages)
        self.assertIn('Договор', text)
        self.assertIn('Подписанный акт приёма-передачи', text)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, ProjectStatus.ACTIVE)

    def test_complete_when_all_checks_pass(self):
        make_ready(self.project, self.head)
        closure.close_project(self.project, self.head)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, ProjectStatus.COMPLETED)
        self.assertEqual(self.project.current_stage, ProjectStageKey.COMPLETED)
        self.assertIsNotNone(self.project.actual_end_date)
        self.assertEqual(self.project.closed_by, self.head)

    def test_complete_releases_team(self):
        make_ready(self.project, self.head)
        user = User.objects.create_user(username='dev')
        member = TeamMember.objects.create(project=self.project, user=user, role=TeamRole.BACKEND, workload=50)
        closure.close_project(self.project, self.head)
        member.refresh_from_db()
        self.assertEqual(member.status, TeamMember.Status.LEFT)
        self.assertIsNotNone(member.left_at)

    def test_force_complete_is_gone(self):
        """Принудительного закрытия нет: ни параметра, ни обходного POST."""
        self.assertFalse(hasattr(delivery, 'complete_project'))
        self.client.force_login(self.head)
        self.client.post(reverse('projects:complete', args=[self.project.pk]),
                         {'force': '1', 'reason': 'Клиент принял без акта'})
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, ProjectStatus.ACTIVE)
        self.assertFalse(self.project.history.filter(field='Завершение (принудительно)').exists())


class DeliveryOnOverviewTests(TestCase):
    """Готовность к закрытию — во вкладке «Обзор», отдельной «Сдачи» нет."""

    def setUp(self):
        doc_services.ensure_default_types()
        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.project = create_project(Project(name="Омур", progress=40))

    def test_overview_shows_completion_block_with_open_checks(self):
        response = self.client.get(self.project.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Готовность к закрытию")
        self.assertContains(response, "Завершить проект")  # кнопка руководителя, пока недоступна
        self.assertNotContains(response, 'data-gp-open="closure-confirm"')
        self.assertFalse(response.context["closure"].ready)

    def test_delivery_tab_is_gone(self):
        response = self.client.get(self.project.get_absolute_url())
        self.assertNotContains(response, "?tab=delivery")

    def test_complete_returns_to_overview(self):
        response = self.client.post(reverse("projects:complete", args=[self.project.pk]))
        self.assertTrue(response.url.endswith("?tab=overview"))
        self.project.refresh_from_db()
        self.assertNotEqual(self.project.status, ProjectStatus.COMPLETED)

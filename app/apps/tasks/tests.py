from django.test import TestCase
from django.urls import reverse

from apps.projects.models import Project, ProjectStageKey
from apps.projects.services import create_project, move_project_to_stage
from apps.tasks.models import Task, TaskStatus, TaskTemplate
from apps.tasks.services import DEFAULT_TEMPLATES, generate_checklist, set_task_status


class ChecklistTests(TestCase):
    """Задачи заводятся руками — автоматических чек-листов больше нет."""

    def test_new_project_has_no_tasks(self):
        project = Project(name='Test')
        create_project(project)
        self.assertEqual(project.tasks.count(), 0)

    def test_move_to_delivery_does_not_create_tasks(self):
        project = Project(name='Test')
        create_project(project)
        move_project_to_stage(project, ProjectStageKey.DELIVERY)
        self.assertEqual(project.tasks.count(), 0)

    def test_checklist_can_still_be_generated_manually(self):
        project = Project(name='Test')
        create_project(project)
        generate_checklist(project, TaskTemplate.Kind.PROJECT_NEW)
        expected = DEFAULT_TEMPLATES[TaskTemplate.Kind.PROJECT_NEW]
        titles = set(project.tasks.values_list('title', flat=True))
        self.assertTrue(set(expected).issubset(titles))

    def test_manual_checklist_not_duplicated(self):
        project = Project(name='Test')
        create_project(project)
        generate_checklist(project, TaskTemplate.Kind.PROJECT_NEW)
        count_before = project.tasks.count()
        generate_checklist(project, TaskTemplate.Kind.PROJECT_NEW)
        self.assertEqual(project.tasks.count(), count_before)


class TaskStatusTests(TestCase):
    def test_done_sets_completed_date(self):
        task = Task.objects.create(title='T')
        set_task_status(task, TaskStatus.DONE)
        self.assertIsNotNone(task.completed_at)

    def test_reopen_clears_completed_date(self):
        task = Task.objects.create(title='T')
        set_task_status(task, TaskStatus.DONE)
        set_task_status(task, TaskStatus.IN_PROGRESS)
        self.assertIsNone(task.completed_at)


class TaskEditDeleteTests(TestCase):
    """Задачу можно отредактировать и удалить из карточки проекта."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.projects.models import Project
        from apps.projects.services import create_project

        self.user = get_user_model().objects.create_user(username="pm", password="x")
        self.client.force_login(self.user)
        self.project = create_project(Project(name="ОБА"))
        self.task = Task.objects.create(
            project=self.project, title="Устроить созвон с тимлид",
        )

    def test_row_has_edit_and_delete(self):
        # этап 7A: на доске — ссылка на карточку задачи, правка и архив — в карточке
        response = self.client.get(f"{self.project.get_absolute_url()}?tab=tasks")
        detail = reverse("projects:task_detail", args=[self.project.pk, self.task.pk])
        self.assertContains(response, detail)
        card = self.client.get(detail)
        self.assertContains(card, reverse("projects:task_update", args=[self.project.pk, self.task.pk]))
        self.assertContains(card, reverse("projects:task_delete", args=[self.project.pk, self.task.pk]))

    def test_delete_archives_task_and_returns_to_project(self):
        # этап 7A: удаление из интерфейса — архив, запись и история остаются
        response = self.client.post(reverse("tasks:delete", args=[self.task.pk]))
        self.task.refresh_from_db()
        self.assertTrue(self.task.is_archived)
        self.assertTrue(self.task.events.filter(kind="archived").exists())
        self.assertTrue(response.url.endswith("?tab=tasks"))

    def test_get_does_not_delete(self):
        self.client.get(reverse("tasks:delete", args=[self.task.pk]))
        self.assertTrue(Task.objects.filter(pk=self.task.pk).exists())

    def test_edit_saves_new_title(self):
        self.client.post(reverse("tasks:update", args=[self.task.pk]), {
            "title": "Созвон с тимлидом", "project": self.project.pk,
            "priority": "medium", "status": "new",
        })
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "Созвон с тимлидом")


class TaskWithoutProjectTests(TestCase):
    """Задачу можно завести без проекта — как личную."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(
            username="head", password="x",
        )
        self.client.force_login(self.user)

    def test_task_saved_without_project(self):
        response = self.client.post(reverse("tasks:create"), {
            "title": "Позвонить в банк", "priority": "medium",
        })
        self.assertEqual(response.status_code, 302)
        task = Task.objects.get(title="Позвонить в банк")
        self.assertIsNone(task.project)

    def test_form_marks_project_optional(self):
        from apps.tasks.forms import TaskForm

        form = TaskForm()
        self.assertFalse(form.fields["project"].required)
        self.assertIn("Без проекта", str(form["project"]))

    def test_list_shows_without_project_label(self):
        Task.objects.create(title="Личная задача")
        response = self.client.get(reverse("tasks:list"))
        self.assertContains(response, "Без проекта")

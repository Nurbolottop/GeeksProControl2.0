from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.documents import services
from apps.documents.models import (
    BRIEF,
    CONTRACT,
    ACCEPTANCE_ACT,
    Document,
    DocumentStatus,
    DocumentTemplate,
    DocumentType,
    REQUIREMENTS,
)
from apps.projects.models import Project


class DocumentProgressTests(TestCase):
    """Контроль комплекта документов (ТЗ §17.1)."""

    def setUp(self):
        services.ensure_default_types()
        self.project = Project.objects.create(name='Test')

    def _add(self, code, **kwargs):
        return Document.objects.create(
            project=self.project,
            doc_type=DocumentType.objects.get(code=code),
            **kwargs,
        )

    def test_progress_counts_required_documents(self):
        progress = services.document_progress(self.project)
        self.assertEqual(progress['done'], 0)
        # бриф, договор, ТЗ, акт приёма-передачи
        self.assertEqual(progress['total'], 4)

        self._add(CONTRACT)
        progress = services.document_progress(self.project)
        self.assertEqual(progress['done'], 1)
        missing_codes = {t.code for t in progress['missing']}
        self.assertEqual(missing_codes, {BRIEF, REQUIREMENTS, ACCEPTANCE_ACT})

    def test_cancelled_document_not_counted(self):
        self._add(CONTRACT, status=DocumentStatus.CANCELLED)
        progress = services.document_progress(self.project)
        self.assertEqual(progress['done'], 0)

    def test_signed_check(self):
        self._add(ACCEPTANCE_ACT)
        self.assertFalse(services.has_signed_document(self.project, ACCEPTANCE_ACT))
        self.project.documents.update(is_signed=True)
        self.assertTrue(services.has_signed_document(self.project, ACCEPTANCE_ACT))


class DocumentTemplateTests(TestCase):
    """Шаблоны документов — второй раздел страницы «Документы», не
    привязаны к проекту (заготовка, которую ПМ берёт за основу)."""

    def setUp(self):
        services.ensure_default_types()
        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.contract_type = DocumentType.objects.get(code=CONTRACT)

    def test_list_page_shows_both_tabs(self):
        response = self.client.get(reverse("documents:list"))
        self.assertContains(response, "Документы проектов")
        self.assertContains(response, "Шаблоны")

    def test_template_list_does_not_require_a_project(self):
        response = self.client.get(reverse("documents:templates"))
        self.assertEqual(response.status_code, 200)

    def test_create_template_without_project(self):
        upload = SimpleUploadedFile(
            "contract_template.txt", b"template text", content_type="text/plain",
        )
        response = self.client.post(reverse("documents:template_create"), {
            "doc_type": self.contract_type.pk,
            "name": "Типовой договор",
            "file": upload,
            "comment": "",
        })
        self.assertRedirects(response, reverse("documents:templates"))
        template = DocumentTemplate.objects.get(name="Типовой договор")
        self.assertEqual(template.doc_type, self.contract_type)
        self.assertEqual(template.uploaded_by, self.user)

    def test_template_shown_in_list(self):
        DocumentTemplate.objects.create(
            doc_type=self.contract_type, name="Договор v2",
        )
        response = self.client.get(reverse("documents:templates"))
        self.assertContains(response, "Договор v2")

    def test_update_template(self):
        template = DocumentTemplate.objects.create(
            doc_type=self.contract_type, name="Старое название",
            file=SimpleUploadedFile("old.txt", b"old", content_type="text/plain"),
        )
        self.client.post(
            reverse("documents:template_update", args=[template.pk]),
            {
                "doc_type": self.contract_type.pk,
                "name": "Новое название",
                "comment": "",
            },
        )
        template.refresh_from_db()
        self.assertEqual(template.name, "Новое название")

    def test_delete_template(self):
        template = DocumentTemplate.objects.create(
            doc_type=self.contract_type, name="На удаление",
        )
        self.client.post(reverse("documents:template_delete", args=[template.pk]))
        self.assertFalse(DocumentTemplate.objects.filter(pk=template.pk).exists())

    def test_rejects_disallowed_extension(self):
        upload = SimpleUploadedFile(
            "template.exe", b"binary", content_type="application/octet-stream",
        )
        self.client.post(reverse("documents:template_create"), {
            "doc_type": self.contract_type.pk, "name": "Плохой файл",
            "file": upload,
        })
        self.assertFalse(
            DocumentTemplate.objects.filter(name="Плохой файл").exists(),
        )


class ProjectBriefTests(TestCase):
    """Бриф по ссылке: ПМ выпускает ссылку под проект, заказчик
    заполняет без входа — контакты уходят в Client, остальное в
    ProjectBrief, плюс заводится Document(doc_type=brief)."""

    def setUp(self):
        from apps.documents.models import ProjectBriefLink

        services.ensure_default_types()
        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="Бриф-проект")
        self.link_model = ProjectBriefLink

    def _payload(self, **overrides):
        data = {
            "organization": "ООО Ромашка", "contact_name": "Иван Иванов",
            "phone": "0700111222", "email": "ivan@example.com",
            "about_business": "Продаём цветы", "goal": "Интернет-магазин",
            "target_audience": "Женщины 20-45", "required_features": "Каталог, корзина",
            "references": "example.com", "deadline_wish": "2 месяца",
            "existing_site_url": "Нет", "domain": "romashka.kg",
            "integrations": "Оплата картой", "languages": "Русский, кыргызский",
            "content_owner": "Мы сами", "social_links": "instagram.com/romashka",
        }
        data.update(overrides)
        return data

    def test_issue_link_deactivates_previous(self):
        first = services.issue_brief_link(self.project, user=self.user)
        second = services.issue_brief_link(self.project, user=self.user)
        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)
        self.assertEqual(services.active_brief_link(self.project), second)

    def test_brief_link_create_view(self):
        response = self.client.post(
            reverse("documents:brief_link_create", args=[self.project.pk]),
        )
        self.assertRedirects(
            response, f"{self.project.get_absolute_url()}?tab=documents",
        )
        self.assertTrue(
            self.link_model.objects.filter(project=self.project, is_active=True).exists(),
        )

    def test_expired_or_missing_link_returns_404(self):
        response = self.client.get(reverse("project_brief_apply", args=["no-such-token"]))
        self.assertEqual(response.status_code, 404)

        link = services.issue_brief_link(self.project)
        link.deactivate()
        self.client.logout()
        response = self.client.get(reverse("project_brief_apply", args=[link.token]))
        self.assertEqual(response.status_code, 404)

    def test_missing_required_field_rejects_submission(self):
        link = services.issue_brief_link(self.project)
        self.client.logout()
        payload = self._payload()
        del payload["goal"]
        response = self.client.post(
            reverse("project_brief_apply", args=[link.token]), payload,
        )
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertIsNone(self.project.client)

    def test_full_submission_creates_client_brief_and_document(self):
        from apps.documents.models import BRIEF, Document, DocumentStatus, ProjectBrief

        link = services.issue_brief_link(self.project, user=self.user)
        self.client.logout()
        response = self.client.post(
            reverse("project_brief_apply", args=[link.token]), self._payload(),
        )
        self.assertEqual(response.status_code, 200)

        self.project.refresh_from_db()
        self.assertIsNotNone(self.project.client)
        self.assertEqual(self.project.client.organization, "ООО Ромашка")
        self.assertEqual(self.project.client.phone, "0700111222")

        brief = ProjectBrief.objects.get(project=self.project)
        self.assertEqual(brief.goal, "Интернет-магазин")
        self.assertIsNotNone(brief.submitted_at)

        document = Document.objects.get(project=self.project, doc_type__code=BRIEF)
        self.assertEqual(document.status, DocumentStatus.DRAFT)

        link.refresh_from_db()
        self.assertEqual(link.submissions, 1)

    def test_resubmission_updates_same_brief_not_duplicate(self):
        from apps.documents.models import BRIEF, Document, ProjectBrief

        link = services.issue_brief_link(self.project)
        self.client.logout()
        self.client.post(
            reverse("project_brief_apply", args=[link.token]), self._payload(),
        )
        self.client.post(
            reverse("project_brief_apply", args=[link.token]),
            self._payload(goal="Обновлённая цель"),
        )
        self.assertEqual(ProjectBrief.objects.filter(project=self.project).count(), 1)
        self.assertEqual(
            Document.objects.filter(project=self.project, doc_type__code=BRIEF).count(), 1,
        )
        brief = ProjectBrief.objects.get(project=self.project)
        self.assertEqual(brief.goal, "Обновлённая цель")

    def test_documents_tab_shows_link_banner_and_submitted_brief(self):
        link = services.issue_brief_link(self.project, user=self.user)
        response = self.client.get(
            f"{self.project.get_absolute_url()}?tab=documents",
        )
        self.assertContains(response, link.token)

        self.client.logout()
        self.client.post(
            reverse("project_brief_apply", args=[link.token]), self._payload(),
        )
        self.client.force_login(self.user)
        response = self.client.get(
            f"{self.project.get_absolute_url()}?tab=documents",
        )
        self.assertContains(response, "Продаём цветы")


class NewProjectBriefTests(TestCase):
    """Бриф на новый проект: заказчик заполняет общую форму, проекта
    ещё нет — заявка превращается в проект одной кнопкой."""

    FILLED = {
        'project_name': 'Магазин запчастей',
        'organization': 'ОсОО Автодеталь',
        'contact_name': 'Иванов Иван',
        'phone': '0700112233',
        'email': 'ivan@example.com',
        'about_business': 'Продаём запчасти',
        'goal': 'Запустить интернет-магазин',
        'target_audience': 'Автовладельцы',
        'required_features': 'Каталог, корзина, оплата',
        'references': 'ozon.ru',
        'deadline_wish': 'до Нового года',
        'existing_site_url': 'нет',
        'domain': 'avtodetal.kg',
        'integrations': '1С',
        'languages': 'русский',
        'content_owner': 'мы сами',
        'social_links': 'instagram.com/avtodetal',
    }

    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # лимит отправок брифа с адреса (UI-03B) — в кэше

    def test_public_form_opens_without_login(self):
        response = self.client.get(reverse('new_project_brief'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Бриф проекта')

    def test_submission_creates_project_with_brief(self):
        """UI-03B: бриф по общей ссылке сразу заводит проект и заказчика."""
        from apps.documents.models import ProjectBrief

        response = self.client.post(reverse('new_project_brief'), self.FILLED)
        self.assertEqual(response.status_code, 200)
        brief = ProjectBrief.objects.get(project_name='Магазин запчастей')
        self.assertIsNotNone(brief.project_id)
        self.assertEqual(brief.project.name, 'Магазин запчастей')
        self.assertEqual(brief.project.client.organization, 'ОсОО Автодеталь')
        self.assertTrue(brief.project.documents.filter(doc_type__code='brief', is_archived=False).exists())
        self.assertIsNotNone(brief.submitted_at)

    def test_head_is_notified(self):
        from apps.notifications.models import Notification

        self.client.post(reverse('new_project_brief'), self.FILLED)
        note = Notification.objects.get(title__startswith='Новый проект с брифа')
        self.assertIn('?tab=documents', note.url)

    def test_requests_page_lists_it(self):
        from django.contrib.auth import get_user_model

        self.client.post(reverse('new_project_brief'), self.FILLED)
        user = get_user_model().objects.create_user(username='head', password='x')
        self.client.force_login(user)
        response = self.client.get(reverse('documents:brief_requests'))
        self.assertContains(response, 'Магазин запчастей')
        self.assertContains(response, 'ОсОО Автодеталь')

    def test_accepting_request_creates_project_and_client(self):
        from django.contrib.auth import get_user_model

        from apps.clients.models import Client as Company
        from apps.documents.models import ProjectBrief
        from apps.projects.models import Project

        self.client.post(reverse('new_project_brief'), self.FILLED)
        brief = ProjectBrief.objects.get(project_name='Магазин запчастей')
        user = get_user_model().objects.create_user(username='head', password='x')
        self.client.force_login(user)
        self.client.post(reverse('documents:brief_request_accept', args=[brief.pk]))
        project = Project.objects.get(name='Магазин запчастей')
        brief.refresh_from_db()
        self.assertEqual(brief.project, project)
        self.assertEqual(project.client, Company.objects.get(organization='ОсОО Автодеталь'))

    def test_accepting_twice_does_not_duplicate(self):
        from django.contrib.auth import get_user_model

        from apps.documents.models import ProjectBrief
        from apps.projects.models import Project

        self.client.post(reverse('new_project_brief'), self.FILLED)
        brief = ProjectBrief.objects.get(project_name='Магазин запчастей')
        user = get_user_model().objects.create_user(username='head', password='x')
        self.client.force_login(user)
        for _ in range(2):
            self.client.post(reverse('documents:brief_request_accept', args=[brief.pk]))
        self.assertEqual(Project.objects.filter(name='Магазин запчастей').count(), 1)

    def test_old_link_brief_still_works(self):
        """Бриф по ссылке проекта не сломался — он по-прежнему про проект."""
        from apps.documents.models import ProjectBriefLink
        from apps.projects.models import Project

        project = Project.objects.create(name='Балажан')
        link = ProjectBriefLink.objects.create(project=project)
        response = self.client.get(link.get_absolute_url())
        self.assertContains(response, 'Балажан')

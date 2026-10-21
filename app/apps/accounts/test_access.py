"""Регрессия разграничения доступа: прямые URL, GET и изменяющие запросы.

Маршруты берутся из реального urlconf, а не из списка в тесте: новая
страница, которую забыли закрыть, сразу уронит test_every_route_*.
"""
import re
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from apps.accounts import access
from apps.accounts.models import User
from apps.documents.models import Document, DocumentType
from apps.interns.models import Intern
from apps.projects.models import Project, ProjectReport
from apps.reserve.models import ReserveCandidate
from apps.teams.models import TeamMember, TeamRole

UserModel = get_user_model()


def _walk(patterns, prefix=''):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern)


def _concrete(route: str) -> str:
    route = route.lstrip('^').rstrip('$')
    route = re.sub(r'<int:[^>]+>', '1', route)
    route = re.sub(r'<(?:str|slug):[^>]+>', 'x', route)
    route = re.sub(r'<path:[^>]+>', 'x/y', route)
    route = re.sub(r'\(\?P<[^>]+>[^)]*\)', 'x', route)
    return '/' + route


def all_routes():
    """Все адреса проекта с подставленными параметрами (без внутренностей /admin/)."""
    urls = sorted({_concrete(r) for r in _walk(get_resolver().url_patterns)})
    return [u for u in urls if not (u.startswith('/admin/') and u != '/admin/')]


def closed_routes():
    return [u for u in all_routes() if not access.is_public_path(u)]


class DefaultDenyTests(TestCase):
    """Стажёр и неизвестные роли: только публичные страницы."""

    def setUp(self):
        self.intern = UserModel.objects.create_user(username='acc-intern', password='x', role=User.Role.INTERN)
        self.unknown = UserModel.objects.create_user(username='acc-unknown', password='x', role=User.Role.INTERN)
        UserModel.objects.filter(pk=self.unknown.pk).update(role='something-new')
        self.empty = UserModel.objects.create_user(username='acc-empty', password='x', role=User.Role.INTERN)
        UserModel.objects.filter(pk=self.empty.pk).update(role='')
        self.staff_intern = UserModel.objects.create_user(
            username='acc-staff', password='x', role=User.Role.INTERN, is_staff=True,
        )

    def test_route_list_is_meaningful(self):
        self.assertGreater(len(closed_routes()), 150)

    def test_every_route_is_closed_on_get(self):
        for user in (self.intern, self.unknown, self.empty, self.staff_intern):
            self.client.force_login(user)
            for url in closed_routes():
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)

    def test_every_route_is_closed_on_post(self):
        self.client.force_login(self.intern)
        counts = (Project.objects.count(), Intern.objects.count(), UserModel.objects.count())
        for url in closed_routes():
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {'name': 'x', 'full_name': 'x'}).status_code, 403)
        self.assertEqual(counts, (Project.objects.count(), Intern.objects.count(), UserModel.objects.count()))

    def test_htmx_request_gets_plain_403(self):
        self.client.force_login(self.intern)
        response = self.client.get(reverse('projects:list'), HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b'<html', response.content)

    def test_intern_cannot_grant_himself_access(self):
        card = Intern.objects.create(full_name='Тест Тестов', user=self.intern)
        self.client.force_login(self.intern)
        response = self.client.post(reverse('interns:grant_access', args=[card.pk]))
        self.assertEqual(response.status_code, 403)
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.role, User.Role.INTERN)

    def test_denied_page_explains_and_offers_logout(self):
        self.client.force_login(self.intern)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Веб-кабинет недоступен', status_code=403)
        self.assertContains(response, reverse('logout'), status_code=403)
        self.assertNotContains(response, 'gp-sidebar', status_code=403)

    def test_public_pages_still_work_for_signed_in_intern(self):
        self.client.force_login(self.intern)
        for url in (reverse('resume_bank_apply'), reverse('talent_reserve_apply'), reverse('new_project_brief'),
                    reverse('healthz'), reverse('login')):
            with self.subTest(url=url):
                self.assertIn(self.client.get(url).status_code, (200, 302))
        self.assertEqual(self.client.get('/brief/no-such-token/').status_code, 404)  # view решает сам
        response = self.client.post(reverse('logout'))
        self.assertEqual(response.status_code, 302)

    def test_public_pages_still_work_for_anonymous(self):
        for url in (reverse('resume_bank_apply'), reverse('talent_reserve_apply'), reverse('new_project_brief'),
                    reverse('login'), reverse('healthz')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_anonymous_is_sent_to_login_not_403(self):
        for url in (reverse('dashboard'), reverse('projects:list'), reverse('interns:list')):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse('login'), response['Location'])


class StaffAccessTests(TestCase):
    """Руководитель, администратор и superuser работают как раньше."""

    def test_full_access_roles(self):
        users = [
            UserModel.objects.create_user(username='acc-head', password='x', role=User.Role.HEAD),
            UserModel.objects.create_user(username='acc-admin', password='x', role=User.Role.ADMINISTRATOR),
            UserModel.objects.create_superuser(username='acc-root', password='x', email='', role=User.Role.INTERN),
        ]
        for user in users:
            self.client.force_login(user)
            for url in (reverse('dashboard'), reverse('projects:list'), reverse('interns:list'), reverse('clients:list')):
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)

    def test_portal_roles_keep_their_redirects(self):
        pm = UserModel.objects.create_user(username='acc-pm', password='x', role=User.Role.PROJECT_MANAGER)
        lead = UserModel.objects.create_user(username='acc-lead', password='x', role=User.Role.TEAM_LEAD)
        self.client.force_login(pm)
        self.assertRedirects(self.client.get(reverse('projects:list')), reverse('pm_portal:dashboard'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('pm_portal:dashboard')).status_code, 200)
        self.client.force_login(lead)
        self.assertRedirects(self.client.get(reverse('interns:list')), reverse('lead_portal:dashboard'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('lead_portal:dashboard')).status_code, 200)


class PortalObjectAccessTests(TestCase):
    """Объекты чужих проектов недоступны ПМ и тимлиду ни на чтение, ни на запись."""

    def setUp(self):
        self.own = Project.objects.create(name='Свой')
        self.foreign = Project.objects.create(name='Чужой')
        self.pm = UserModel.objects.create_user(username='obj-pm', password='x', role=User.Role.PROJECT_MANAGER)
        pm_card = Intern.objects.create(full_name='ПМ Тестов', user=self.pm)
        TeamMember.objects.create(project=self.own, intern=pm_card, role=TeamRole.PROJECT_MANAGER, status=TeamMember.Status.ACTIVE)
        self.lead = UserModel.objects.create_user(username='obj-lead', password='x', role=User.Role.TEAM_LEAD)
        lead_card = Intern.objects.create(full_name='Тимлид Тестов', user=self.lead)
        TeamMember.objects.create(project=self.own, intern=lead_card, role=TeamRole.TEAM_LEAD, status=TeamMember.Status.ACTIVE)

        stranger = Intern.objects.create(full_name='Чужой Участник')
        self.foreign_member = TeamMember.objects.create(
            project=self.foreign, intern=stranger, role=TeamRole.BACKEND, status=TeamMember.Status.ACTIVE,
        )
        self.foreign_report = ProjectReport.objects.create(project=self.foreign, text='Чужой отчёт')
        doc_type = DocumentType.objects.create(code='gp2-access', name='Тестовый')
        self.foreign_doc = Document.objects.create(project=self.foreign, doc_type=doc_type)

    def test_pm_cannot_read_or_change_foreign_objects(self):
        self.client.force_login(self.pm)
        f = self.foreign.pk
        for url in (
            reverse('pm_portal:project_detail', args=[f]),
            reverse('pm_portal:report_update', args=[f, self.foreign_report.pk]),
            reverse('pm_portal:member_edit', args=[f, self.foreign_member.pk]),
            reverse('pm_portal:client_edit', args=[f]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        for url in (
            reverse('pm_portal:report_update', args=[f, self.foreign_report.pk]),
            reverse('pm_portal:report_delete', args=[f, self.foreign_report.pk]),
            reverse('pm_portal:member_delete', args=[f, self.foreign_member.pk]),
            reverse('pm_portal:document_delete', args=[f, self.foreign_doc.pk]),
            reverse('pm_portal:document_upload', args=[f]),
            reverse('pm_portal:report_create', args=[f]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {'text': 'взлом'}).status_code, 404)
        self.assertTrue(ProjectReport.objects.filter(pk=self.foreign_report.pk, text='Чужой отчёт').exists())
        self.assertTrue(TeamMember.objects.filter(pk=self.foreign_member.pk).exists())
        self.assertFalse(ProjectReport.objects.filter(text='взлом').exists())

    def test_pm_cannot_reach_foreign_object_through_own_project(self):
        """Свой проект в URL, но чужой отчёт/участник/документ — тоже 404."""
        self.client.force_login(self.pm)
        own = self.own.pk
        self.assertEqual(self.client.post(reverse('pm_portal:report_delete', args=[own, self.foreign_report.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('pm_portal:member_delete', args=[own, self.foreign_member.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('pm_portal:document_delete', args=[own, self.foreign_doc.pk])).status_code, 404)
        self.assertTrue(TeamMember.objects.filter(pk=self.foreign_member.pk).exists())
        self.foreign_doc.refresh_from_db()
        self.assertFalse(self.foreign_doc.is_archived)

    def test_lead_cannot_read_or_change_foreign_objects(self):
        self.client.force_login(self.lead)
        f = self.foreign.pk
        self.assertEqual(self.client.get(reverse('lead_portal:project_detail', args=[f])).status_code, 404)
        self.assertEqual(self.client.post(reverse('lead_portal:member_delete', args=[f, self.foreign_member.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('lead_portal:member_delete', args=[self.own.pk, self.foreign_member.pk])).status_code, 404)
        self.assertEqual(
            self.client.get(reverse('lead_portal:intern_detail', args=[self.own.pk, self.foreign_member.intern_id])).status_code, 404,
        )
        self.assertTrue(TeamMember.objects.filter(pk=self.foreign_member.pk).exists())


class MediaAccessTests(TestCase):
    """Файлы /media/: право на конкретный файл, а не просто «вошёл»."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.tmp.name, MEDIA_ACCEL_REDIRECT_PREFIX='')
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.own = Project.objects.create(name='Свой')
        self.foreign = Project.objects.create(name='Чужой')
        doc_type = DocumentType.objects.create(code='gp2-media', name='Тестовый')
        self.own_doc = Document.objects.create(project=self.own, doc_type=doc_type, file=SimpleUploadedFile('own.txt', b'own'))
        self.foreign_doc = Document.objects.create(project=self.foreign, doc_type=doc_type, file=SimpleUploadedFile('foreign.txt', b'foreign'))

        self.pm = UserModel.objects.create_user(username='media-pm', password='x', role=User.Role.PROJECT_MANAGER)
        card = Intern.objects.create(full_name='ПМ Медиа', user=self.pm)
        TeamMember.objects.create(project=self.own, intern=card, role=TeamRole.PROJECT_MANAGER, status=TeamMember.Status.ACTIVE)

        self.lead = UserModel.objects.create_user(username='media-lead', password='x', role=User.Role.TEAM_LEAD)
        lead_card = Intern.objects.create(full_name='Тимлид Медиа', user=self.lead)
        self.own_resume = ReserveCandidate.objects.create(
            full_name='Тимлид Медиа', intern=lead_card, resume_file=SimpleUploadedFile('lead.pdf', b'cv'),
        )
        other = ReserveCandidate.objects.create(full_name='Другой Кандидат', resume_file=SimpleUploadedFile('other.pdf', b'cv2'))
        self.other_resume = other

    def url(self, field):
        return '/media/' + field.name

    def test_head_opens_any_file(self):
        head = UserModel.objects.create_user(username='media-head', password='x', role=User.Role.HEAD)
        self.client.force_login(head)
        for field in (self.own_doc.file, self.foreign_doc.file, self.other_resume.resume_file):
            self.assertEqual(self.client.get(self.url(field)).status_code, 200)

    def test_pm_opens_only_own_project_documents(self):
        self.client.force_login(self.pm)
        self.assertEqual(self.client.get(self.url(self.own_doc.file)).status_code, 200)
        self.assertEqual(self.client.get(self.url(self.foreign_doc.file)).status_code, 404)
        self.assertEqual(self.client.get(self.url(self.other_resume.resume_file)).status_code, 404)

    def test_lead_opens_own_resume_and_documents_of_assigned_projects(self):
        self.client.force_login(self.lead)
        self.assertEqual(self.client.get(self.url(self.own_resume.resume_file)).status_code, 200)
        self.assertEqual(self.client.get(self.url(self.other_resume.resume_file)).status_code, 404)
        # не закреплён за проектом — документа не видит
        self.assertEqual(self.client.get(self.url(self.own_doc.file)).status_code, 404)
        TeamMember.objects.create(project=self.own, intern=self.lead.intern_profile, role=TeamRole.TEAM_LEAD,
                                  status=TeamMember.Status.ACTIVE)
        self.assertEqual(self.client.get(self.url(self.own_doc.file)).status_code, 200)
        self.assertEqual(self.client.get(self.url(self.foreign_doc.file)).status_code, 404)

    def test_outsider_staff_cannot_open_foreign_documents(self):
        """ПМ другого проекта и тимлид другого проекта — 404 на чужой документ."""
        other_pm = UserModel.objects.create_user(username='media-pm2', password='x', role=User.Role.PROJECT_MANAGER)
        card = Intern.objects.create(full_name='ПМ Другой', user=other_pm)
        TeamMember.objects.create(project=self.foreign, intern=card, role=TeamRole.PROJECT_MANAGER, status=TeamMember.Status.ACTIVE)
        self.client.force_login(other_pm)
        self.assertEqual(self.client.get(self.url(self.own_doc.file)).status_code, 404)
        self.assertEqual(self.client.get(self.url(self.foreign_doc.file)).status_code, 200)

    def test_direct_url_variants_do_not_bypass(self):
        self.client.force_login(self.pm)
        name = self.foreign_doc.file.name
        for url in ('/media/' + name, '/media//' + name, '/media/./' + name,
                    '/media/documents/../' + name):
            with self.subTest(url=url):
                self.assertIn(self.client.get(url).status_code, (301, 404))

    def test_intern_and_anonymous_get_nothing_private(self):
        intern = UserModel.objects.create_user(username='media-intern', password='x', role=User.Role.INTERN)
        self.client.force_login(intern)
        self.assertEqual(self.client.get(self.url(self.own_doc.file)).status_code, 404)
        self.client.logout()
        response = self.client.get(self.url(self.own_doc.file))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_public_photo_stays_public(self):
        photo_dir = Path(self.tmp.name) / 'reserve' / 'photo'
        photo_dir.mkdir(parents=True)
        (photo_dir / 'face.jpg').write_bytes(b'jpg')
        self.assertEqual(self.client.get('/media/reserve/photo/face.jpg').status_code, 200)


class CatalogAccessTests(TestCase):
    def test_catalog_is_staff_only_and_absent_in_production(self):
        head = UserModel.objects.create_user(username='cat-head', password='x', role=User.Role.HEAD)
        staff_intern = UserModel.objects.create_user(username='cat-staff', password='x', role=User.Role.INTERN, is_staff=True)
        self.client.force_login(staff_intern)
        self.assertEqual(self.client.get(reverse('ui:catalog')).status_code, 403)
        self.client.force_login(head)
        self.assertEqual(self.client.get(reverse('ui:catalog')).status_code, 200)
        with override_settings(GP2_ENVIRONMENT='production'):
            self.assertEqual(self.client.get(reverse('ui:catalog')).status_code, 404)
            self.assertEqual(self.client.post(reverse('ui:catalog_toast')).status_code, 404)

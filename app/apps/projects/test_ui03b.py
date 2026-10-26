"""UI-03B: закрытый набор, четыре обязательных документа, бриф, закрытие
проекта руководителем и автоматический выпуск."""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.dashboard.graduation import compute
from apps.documents import project_documents as docs
from apps.documents import services as doc_services
from apps.documents.models import Document, ProjectBrief, ProjectBriefLink
from apps.interns.models import GraduateStatus, Intern, InternStatus, ProfileFormLink
from apps.projects import closure
from apps.projects.fixtures_closure import head, make_ready, pdf, upload_required
from apps.projects.forms import ProjectDetailsForm, ProjectForm
from apps.projects.models import Project, ProjectStatus, ProjectStatusHistory
from apps.projects.services import create_project, set_recruitment
from apps.teams.models import TeamMember, TeamRole
from apps.teams.rules import check_assignment
from apps.teams.services import save_membership
from apps.training.models import Specialization

UserModel = get_user_model()
END = datetime.date(2026, 9, 30)


def portal(name, role, team_role, *projects):
    user = UserModel.objects.create_user(username=name, password='x', role=role)
    intern = Intern.objects.create(full_name=name, user=user)
    for project in projects:
        TeamMember.objects.create(project=project, intern=intern, role=team_role, status=TeamMember.Status.ACTIVE)
    return user, intern


def graduate(name, spec=None):
    done = Project.objects.create(name=f'Сданный {name}', status=ProjectStatus.COMPLETED, actual_end_date=END)
    person = Intern.objects.create(full_name=name, specialization=spec, status=InternStatus.ACTIVE)
    TeamMember.objects.create(project=done, intern=person, role=TeamRole.BACKEND, status=TeamMember.Status.LEFT,
                              left_reason=TeamMember.LeftReason.PROJECT_ENDED, left_at=END)
    return person


def member(project, intern, role=TeamRole.BACKEND, **extra):
    return TeamMember.objects.create(project=project, intern=intern, role=role, status=TeamMember.Status.ACTIVE, **extra)


# ======================================================================== набор

class ClosedRecruitmentTests(TestCase):
    """Закрытый набор блокирует все пути вступления; кто уже в команде — работает."""

    def setUp(self):
        self.backend = Specialization.objects.create(name='Backend')
        self.head = head()
        self.project = create_project(Project(name='Закрытый'))  # набор по умолчанию закрыт
        self.novice = Intern.objects.create(full_name='Новичок', specialization=self.backend, status=InternStatus.WAITING)

    def assertNotMember(self, intern=None):
        self.assertFalse(TeamMember.objects.filter(project=self.project, intern=intern or self.novice,
                                                   status=TeamMember.Status.ACTIVE).exists())

    def test_rule_reports_closed_recruitment(self):
        errors, _ = check_assignment(self.novice, self.project, TeamRole.BACKEND)
        self.assertTrue(any('Набор' in e and 'закрыт' in e for e in errors))
        set_recruitment(self.project, True, self.head)
        self.project.refresh_from_db()
        self.assertEqual(check_assignment(self.novice, self.project, TeamRole.BACKEND)[0], [])

    def test_recruitment_by_direction(self):
        """11F: набор открыт только на выбранные направления; пусто — на все."""
        set_recruitment(self.project, True, self.head, roles=['uxui', 'backend', 'uxui'])
        self.project.refresh_from_db()
        self.assertEqual(self.project.recruitment_roles, ['backend', 'uxui'])  # порядок направлений, без повторов
        self.assertEqual(check_assignment(self.novice, self.project, TeamRole.BACKEND)[0], [])
        errors, _ = check_assignment(self.novice, self.project, TeamRole.FRONTEND)
        self.assertTrue(any('Frontend' in e and 'открыт только на Backend, UX/UI' in e for e in errors), errors)
        # при открытом наборе меняются только направления — и это видно в истории
        set_recruitment(self.project, True, self.head, roles=[])
        self.project.refresh_from_db()
        self.assertEqual(self.project.recruitment_roles, [])
        self.assertEqual(check_assignment(self.novice, self.project, TeamRole.FRONTEND)[0], [])
        last = ProjectStatusHistory.objects.filter(project=self.project, field='Набор').latest('pk')
        self.assertEqual((last.old_value, last.new_value), ('открыт: Backend, UX/UI', 'открыт (все направления)'))
        set_recruitment(self.project, False, self.head)
        self.project.refresh_from_db()
        self.assertEqual((self.project.recruitment_open, self.project.recruitment_roles), (False, []))
        with self.assertRaises(ValidationError):
            set_recruitment(self.project, True, self.head, roles=['pm'])

    def test_recruitment_for_new_or_old(self):
        """11F: набор только для новых / только для старых (выпускников)."""
        veteran = graduate('Старый Выпускник', self.backend)
        set_recruitment(self.project, True, self.head, kind='new')
        self.project.refresh_from_db()
        self.assertEqual(check_assignment(self.novice, self.project, TeamRole.BACKEND)[0], [])
        errors, _ = check_assignment(veteran, self.project, TeamRole.BACKEND)
        self.assertTrue(any('только для новых' in e for e in errors), errors)
        set_recruitment(self.project, True, self.head, kind='old')
        self.project.refresh_from_db()
        self.assertEqual(check_assignment(veteran, self.project, TeamRole.BACKEND)[0], [])
        errors, _ = check_assignment(self.novice, self.project, TeamRole.BACKEND)
        self.assertTrue(any('только для старых' in e for e in errors), errors)
        last = ProjectStatusHistory.objects.filter(project=self.project, field='Набор').latest('pk')
        self.assertEqual((last.old_value, last.new_value),
                         ('открыт (все направления) · только новые', 'открыт (все направления) · только старые'))
        set_recruitment(self.project, False, self.head)
        self.project.refresh_from_db()
        self.assertEqual(self.project.recruitment_kind, 'all')  # закрыли — при следующем открытии снова «всех»
        with self.assertRaises(ValidationError):
            set_recruitment(self.project, True, self.head, kind='vip')

    def test_recruitment_form_on_card(self):
        self.client.force_login(self.head)
        url = reverse('projects:recruitment_set', args=[self.project.pk])
        self.client.post(url, {'open': '1', 'roles': ['qa', 'mobile'], 'kind': 'new', 'tab': 'team'})
        self.project.refresh_from_db()
        self.assertEqual((self.project.recruitment_open, self.project.recruitment_roles, self.project.recruitment_kind),
                         (True, ['mobile', 'qa'], 'new'))
        page = self.client.get(reverse('projects:detail', args=[self.project.pk]), {'tab': 'team'})
        self.assertContains(page, 'Mobile, QA · только новые')
        self.assertContains(page, 'Кого берём')
        self.assertContains(page, 'Сохранить набор')

    def test_recruitment_board(self):
        """11F: страница «Наборы» — все идущие проекты, форма возвращает на неё."""
        self.client.force_login(self.head)
        board = reverse('projects:recruitment_board')
        page = self.client.get(board)
        self.assertContains(page, 'Закрытый')
        self.assertContains(page, 'Открыть набор')
        member(self.project, self.novice, TeamRole.UXUI)
        member(self.project, graduate('Старый Дизайнер', self.backend), TeamRole.BACKEND)
        page = self.client.get(board)
        self.assertContains(page, 'Новые стажёры: 1')
        self.assertContains(page, 'Старые стажёры: 1')
        self.assertContains(page, 'UX/UI: 1')
        self.assertContains(page, 'Backend: 1')
        response = self.client.post(reverse('projects:recruitment_set', args=[self.project.pk]),
                                    {'open': '1', 'roles': ['backend'], 'kind': 'new', 'back': 'board'})
        self.assertRedirects(response, f'{board}#project-{self.project.pk}', fetch_redirect_response=False)
        self.project.refresh_from_db()
        self.assertEqual(self.project.recruitment_summary, 'Backend · только новые')
        pm_user, _ = portal('pm-board', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.project)
        self.client.force_login(pm_user)
        self.assertNotEqual(self.client.get(board).status_code, 200)

    def test_head_form_is_blocked(self):
        self.client.force_login(self.head)
        response = self.client.post(reverse('teams:member_add', args=[self.project.pk]), {'intern': self.novice.pk})
        self.assertContains(response, 'закрыт')
        self.assertNotMember()

    def test_pm_form_is_blocked(self):
        pm, _ = portal('rb-pm', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.project)
        self.client.force_login(pm)
        self.client.post(reverse('pm_portal:member_add', args=[self.project.pk]), {'intern': self.novice.pk})
        self.assertNotMember()

    def test_intern_card_add_is_blocked(self):
        self.client.force_login(self.head)
        self.client.post(reverse('interns:project_add', args=[self.novice.pk]), {'project': self.project.pk})
        self.assertNotMember()

    def test_old_direct_form_link_is_blocked(self):
        from apps.interns.services import join_project_from_form

        link = ProfileFormLink.objects.create(project=self.project)  # ссылка, выпущенная до закрытия
        self.assertIsNone(join_project_from_form(self.novice, link))
        self.assertNotMember()

    def test_public_form_page_refuses_and_saves_nothing(self):
        link = ProfileFormLink.objects.create(project=self.project)
        url = reverse('intern_profile_apply', args=[link.token])
        self.assertEqual(self.client.get(url).status_code, 403)
        payload = {'full_name': 'Чужой', 'phone': '0700999000', 'email': 'x@example.com', 'telegram': '@x',
                   'city': 'Бишкек', 'branch': 'Бишкек', 'specialization': self.backend.pk,
                   'education_end_date': '2026-06-01', 'internship_start_date': '2026-07-01', 'internship_attempt': '1'}
        for _ in range(2):  # повторная отправка тоже
            response = self.client.post(url, payload)
            self.assertEqual(response.status_code, 403)
        self.assertFalse(Intern.objects.filter(phone='0700999000').exists())

    def test_lead_cannot_issue_form_link_while_closed(self):
        lead, _ = portal('rb-lead', User.Role.TEAM_LEAD, TeamRole.TEAM_LEAD, self.project)
        self.client.force_login(lead)
        self.client.post(reverse('lead_portal:profile_link_create', args=[self.project.pk]), {'ttl_days': '7'})
        self.assertFalse(ProfileFormLink.objects.filter(project=self.project, is_active=True).exists())

    def test_reactivating_left_member_is_a_join(self):
        gone = member(self.project, self.novice)
        TeamMember.objects.filter(pk=gone.pk).update(status=TeamMember.Status.LEFT)
        gone.refresh_from_db()
        gone.status = TeamMember.Status.ACTIVE
        self.assertIn('закрыт', save_membership(gone))
        gone.refresh_from_db()
        self.assertEqual(gone.status, TeamMember.Status.LEFT)

    def test_existing_members_keep_working(self):
        set_recruitment(self.project, True, self.head)
        current = member(self.project, self.novice)
        set_recruitment(self.project, False, self.head)
        self.project.refresh_from_db()
        self.assertEqual(check_assignment(self.novice, self.project, TeamRole.BACKEND, exclude_member_pk=current.pk)[0], [])
        current.comment = 'правка'
        self.assertIsNone(save_membership(current))
        current.refresh_from_db()
        self.assertEqual(current.comment, 'правка')

    def test_reopening_resumes_joining(self):
        from apps.interns.services import join_project_from_form

        link = ProfileFormLink.objects.create(project=self.project)
        self.assertIsNone(join_project_from_form(self.novice, link))
        set_recruitment(self.project, True, self.head)
        self.project.refresh_from_db()
        self.assertIsNotNone(join_project_from_form(self.novice, link))
        self.assertEqual(TeamMember.objects.filter(project=self.project, intern=self.novice).count(), 1)

    def test_repeat_join_does_not_duplicate(self):
        from apps.interns.services import join_project_from_form

        set_recruitment(self.project, True, self.head)
        link = ProfileFormLink.objects.create(project=self.project)
        for _ in range(3):
            join_project_from_form(self.novice, link)
        self.assertEqual(TeamMember.objects.filter(project=self.project, intern=self.novice).count(), 1)

    def test_recruitment_closed_between_validation_and_save(self):
        """Гонка: форма прошла проверку, набор закрыли, затем сохранение — отказ."""
        from apps.teams.forms import TeamMemberForm

        set_recruitment(self.project, True, self.head)
        self.project.refresh_from_db()
        form = TeamMemberForm({'intern': self.novice.pk}, role=TeamRole.BACKEND, project=self.project)
        self.assertTrue(form.is_valid(), form.errors)
        set_recruitment(self.project, False, self.head)
        pending = form.save(commit=False)
        pending.project = self.project
        self.assertIn('закрыт', save_membership(pending))
        self.assertNotMember()

    def test_staff_assignment_is_not_recruitment(self):
        """ПМ/тимлида назначает руководство — закрытый набор это не блокирует."""
        lead = Intern.objects.create(full_name='Назначаемый Тимлид', specialization=self.backend)
        self.assertEqual(check_assignment(lead, self.project, TeamRole.TEAM_LEAD)[0], [])
        self.assertEqual(check_assignment(lead, self.project, TeamRole.PROJECT_MANAGER)[0], [])

    def test_finished_project_takes_nobody_even_if_flag_open(self):
        Project.objects.filter(pk=self.project.pk).update(recruitment_open=True, status=ProjectStatus.COMPLETED)
        self.project.refresh_from_db()
        errors, _ = check_assignment(self.novice, self.project, TeamRole.BACKEND)
        self.assertTrue(errors)

    def test_recruitment_opens_only_for_running_project(self):
        Project.objects.filter(pk=self.project.pk).update(status=ProjectStatus.CANCELLED)
        with self.assertRaises(ValidationError):
            set_recruitment(Project.objects.get(pk=self.project.pk), True, self.head)


# ==================================================================== документы

class DocumentRightsTests(TestCase):
    def setUp(self):
        doc_services.ensure_default_types()
        self.head = head()
        self.project = create_project(Project(name='Документы'))
        self.other = create_project(Project(name='Чужой'))
        self.pm, _ = portal('d-pm', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.project)
        self.lead, _ = portal('d-lead', User.Role.TEAM_LEAD, TeamRole.TEAM_LEAD, self.project)
        self.pm_other, _ = portal('d-pm2', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.other)
        self.lead_other, _ = portal('d-lead2', User.Role.TEAM_LEAD, TeamRole.TEAM_LEAD, self.other)
        self.intern_user = UserModel.objects.create_user(username='d-intern', password='x', role=User.Role.INTERN)

    def upload_url(self, scope, project=None):
        prefix = {'head': 'projects', 'pm': 'pm_portal', 'lead': 'lead_portal'}[scope]
        return reverse(f'{prefix}:document_upload', args=[(project or self.project).pk])

    def test_all_four_types_by_head_pm_and_lead(self):
        for user, scope in ((self.head, 'head'), (self.pm, 'pm'), (self.lead, 'lead')):
            self.client.force_login(user)
            for code, _label in docs.REQUIRED_DOCUMENTS:
                with self.subTest(user=user.username, code=code):
                    self.client.post(self.upload_url(scope), {'doc_type': code, 'file': pdf(f'{code}.pdf')})
                    current = docs.current(self.project, code)
                    self.assertIsNotNone(current)
                    self.assertEqual(current.uploaded_by, user)
        # каждая следующая загрузка — новая версия, прежние в истории
        self.assertEqual(Document.objects.filter(project=self.project, doc_type__code='contract').count(), 3)
        self.assertEqual(Document.objects.filter(project=self.project, is_archived=False).count(), 4)

    def test_unknown_type_and_bad_file_are_rejected(self):
        self.client.force_login(self.head)
        self.client.post(self.upload_url('head'), {'doc_type': 'passport', 'file': pdf()})
        bad = SimpleUploadedFile('virus.exe', b'MZ', content_type='application/octet-stream')
        self.client.post(self.upload_url('head'), {'doc_type': 'contract', 'file': bad})
        self.client.post(self.upload_url('head'), {'doc_type': 'contract'})
        self.assertFalse(Document.objects.filter(project=self.project).exists())

    def test_foreign_pm_lead_and_intern_get_404(self):
        cases = ((self.pm_other, 'pm'), (self.lead_other, 'lead'))
        for user, scope in cases:
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.post(self.upload_url(scope), {'doc_type': 'contract', 'file': pdf()}).status_code, 404)
                detail = 'pm_portal:project_detail' if scope == 'pm' else 'lead_portal:project_detail'
                self.assertEqual(self.client.get(reverse(detail, args=[self.project.pk]), {'tab': 'documents'}).status_code, 404)
        self.client.force_login(self.intern_user)
        response = self.client.post(self.upload_url('head'), {'doc_type': 'contract', 'file': pdf()})
        self.assertIn(response.status_code, (302, 403, 404))
        self.assertFalse(Document.objects.filter(project=self.project).exists())

    def test_pm_cannot_use_head_urls(self):
        self.client.force_login(self.pm)
        response = self.client.post(self.upload_url('head'), {'doc_type': 'contract', 'file': pdf()})
        self.assertNotEqual(response.status_code, 200)
        self.assertFalse(Document.objects.filter(project=self.project).exists())

    def test_foreign_document_through_own_project_url(self):
        foreign = docs.upload(self.other, 'contract', pdf(), self.head)
        self.client.force_login(self.pm)
        response = self.client.post(reverse('pm_portal:document_delete', args=[self.project.pk, foreign.pk]))
        self.assertEqual(response.status_code, 404)
        foreign.refresh_from_db()
        self.assertFalse(foreign.is_archived)

    def test_media_direct_links(self):
        document = docs.upload(self.project, 'contract', pdf(), self.head)
        replacement = docs.upload(self.project, 'contract', pdf('v2.pdf'), self.head)
        url = document.file.url
        for user, expected in ((self.pm, 200), (self.lead, 200), (self.head, 200),
                               (self.pm_other, 404), (self.lead_other, 404), (self.intern_user, 404)):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.client.get(url).status_code, expected)  # и старая версия
        self.client.logout()
        self.assertEqual(self.client.get(replacement.file.url).status_code, 302)  # аноним — на вход

    def test_file_name_is_random(self):
        document = docs.upload(self.project, 'contract', pdf('Договор Иванов.pdf'), self.head)
        self.assertNotIn('Иванов', document.file.name)
        self.assertTrue(document.file.name.startswith(f'documents/{self.project.pk}/'))
        self.assertEqual(document.original_name, 'Договор Иванов.pdf')

    def test_replace_and_delete_keep_history_and_log(self):
        first = docs.upload(self.project, 'requirements', pdf(), self.pm)
        second = docs.upload(self.project, 'requirements', pdf(), self.lead)
        first.refresh_from_db()
        self.assertEqual((first.is_archived, first.removed_reason, first.removed_by), (True, 'replaced', self.lead))
        docs.delete(second, self.pm)
        second.refresh_from_db()
        self.assertEqual((second.is_archived, second.removed_reason), (True, 'deleted'))
        self.assertIsNone(docs.current(self.project, 'requirements'))
        self.assertEqual(len(docs.versions(self.project, 'requirements')), 2)
        texts = list(ProjectStatusHistory.objects.filter(project=self.project, field='Документы').values_list('new_value', flat=True))
        self.assertTrue(any(t.startswith('Загружен: ТЗ') for t in texts))
        self.assertTrue(any(t.startswith('Заменён: ТЗ') for t in texts))
        self.assertIn('Удалён: ТЗ', texts)

    def test_deleting_required_document_affects_readiness_at_once(self):
        make_ready(self.project, self.head)
        self.assertTrue(closure.readiness(self.project).ready)
        docs.delete(docs.current(self.project, 'contract'), self.pm)
        state = closure.readiness(self.project)
        self.assertFalse(state.ready)
        self.assertEqual([m['label'] for m in state.missing], ['Договор'])

    def test_completed_project_is_head_only_with_reason(self):
        make_ready(self.project, self.head)
        closure.close_project(self.project, self.head)
        self.project.refresh_from_db()
        act = docs.current(self.project, 'acceptance_act')
        for user in (self.pm, self.lead):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                docs.upload(self.project, 'contract', pdf(), user)
            with self.assertRaises(PermissionDenied):
                docs.delete(act, user)
        with self.assertRaises(ValidationError):
            docs.upload(self.project, 'contract', pdf(), self.head)  # без причины
        docs.upload(self.project, 'contract', pdf(), self.head, reason='Перепутали файл договора')
        record = ProjectStatusHistory.objects.filter(project=self.project, field='Документы').latest('created_at')
        self.assertIn('Исправление закрытого проекта', record.reason)
        self.assertIn('Перепутали файл', record.reason)

    def test_lead_of_finished_project_sees_but_cannot_edit(self):
        make_ready(self.project, self.head)
        closure.close_project(self.project, self.head)
        rights = docs.rights(self.lead, Project.objects.get(pk=self.project.pk))
        self.assertTrue(rights.view)
        self.assertFalse(rights.edit)
        self.client.force_login(self.lead)
        response = self.client.get(reverse('lead_portal:project_detail', args=[self.project.pk]), {'tab': 'documents'})
        self.assertContains(response, 'только для просмотра')
        self.assertNotContains(response, 'doc-upload-form-contract')

    def test_tabs_render_for_each_role(self):
        docs.upload(self.project, 'contract', pdf(), self.pm, title='Договор оказания услуг')
        for user, url in ((self.head, f'{self.project.get_absolute_url()}?tab=documents'),
                          (self.pm, reverse('pm_portal:project_detail', args=[self.project.pk]) + '?tab=documents'),
                          (self.lead, reverse('lead_portal:project_detail', args=[self.project.pk]) + '?tab=documents')):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                response = self.client.get(url)
                self.assertContains(response, 'Обязательные документы')
                self.assertContains(response, 'Договор оказания услуг')
                self.assertContains(response, '1 из 4')
                self.assertContains(response, 'd-pm')  # автор


# ========================================================================= бриф

class BriefTests(TestCase):
    FILLED = {
        'project_name': 'Сайт пекарни', 'organization': 'ОсОО Хлеб', 'contact_name': 'Тестов Тест',
        'phone': '+996 700 11-22-33', 'email': 'bread@example.com', 'about_business': 'Пекарня',
        'goal': 'Сайт', 'target_audience': 'Горожане', 'required_features': 'Меню', 'references': 'нет',
        'deadline_wish': 'месяц', 'existing_site_url': 'нет', 'domain': 'bread.kg', 'integrations': 'нет',
        'languages': 'русский', 'content_owner': 'мы', 'social_links': 'нет',
    }

    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # лимит отправок с адреса считается в кэше

    def post(self, data=None, client=None):
        return (client or self.client).post(reverse('new_project_brief'), data or self.FILLED)

    def test_public_brief_creates_project_brief_and_notification(self):
        from apps.notifications.models import Notification

        self.post()
        brief = ProjectBrief.objects.get(project_name='Сайт пекарни')
        project = brief.project
        self.assertEqual(project.client.organization, 'ОсОО Хлеб')
        self.assertIsNotNone(docs.current(project, 'brief'))
        self.assertTrue(Notification.objects.filter(url__contains=project.get_absolute_url()).exists())
        self.assertFalse(project.recruitment_open)

    def test_duplicate_submission_does_not_create_second_project(self):
        self.post()
        other = self.client_class()
        self.post(dict(self.FILLED, phone='+996700112233'), client=other)  # тот же номер в другом формате
        self.assertEqual(Project.objects.filter(name='Сайт пекарни').count(), 1)

    def test_same_browser_submits_once(self):
        self.post()
        self.post(dict(self.FILLED, project_name='Ещё проект', phone='0555000000'))
        self.assertFalse(Project.objects.filter(name='Ещё проект').exists())

    def test_honeypot_saves_nothing(self):
        response = self.post(dict(self.FILLED, website='http://spam.example'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProjectBrief.objects.exists())
        self.assertFalse(Project.objects.filter(name='Сайт пекарни').exists())

    def test_rate_limit_per_address(self):
        from django.core.cache import cache

        cache.clear()
        for index in range(6):
            response = self.post(dict(self.FILLED, project_name=f'Проект {index}', phone=f'070000000{index}'),
                                 client=self.client_class())
        self.assertEqual(response.status_code, 429)
        self.assertEqual(ProjectBrief.objects.count(), 5)
        cache.clear()

    def test_link_brief_shows_no_internal_info(self):
        project = create_project(Project(name='Клиентский', head_comment='Внутренний комментарий руководителя'))
        portal('Внутренний ПМ', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, project)
        link = doc_services.issue_brief_link(project)
        page = self.client.get(reverse('project_brief_apply', args=[link.token]))
        self.assertContains(page, 'Клиентский')
        self.assertNotContains(page, 'Внутренний комментарий')
        self.assertNotContains(page, 'Внутренний ПМ')
        self.assertNotContains(page, 'Активный')

    def test_link_brief_closed_for_completed_project(self):
        project = create_project(Project(name='Сданный'))
        link = doc_services.issue_brief_link(project)
        Project.objects.filter(pk=project.pk).update(status=ProjectStatus.COMPLETED)
        self.assertEqual(self.client.get(reverse('project_brief_apply', args=[link.token])).status_code, 404)

    def test_pages_render_in_new_ui(self):
        self.post()
        self.client.force_login(head())
        for url in (reverse('documents:brief_requests'), reverse('clients:list'), reverse('documents:list')):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'gp-legacy')
        client = ProjectBrief.objects.get().project.client
        response = self.client.get(reverse('clients:detail', args=[client.pk]))
        self.assertContains(response, 'Сайт пекарни')
        self.assertNotContains(response, 'gp-legacy')


# ===================================================================== закрытие

class ClosureTests(TestCase):
    def setUp(self):
        doc_services.ensure_default_types()
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.project = create_project(Project(name='Альфа'))
        self.pm_user, self.pm = portal('c-pm', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.project)

    def test_exactly_seven_conditions_and_info_does_not_block(self):
        state = closure.readiness(self.project)
        self.assertEqual([i['label'] for i in state.items], [
            'Разработка завершена', 'Проект опубликован в production', 'Проект принят заказчиком',
            'Бриф заказчика', 'Договор', 'ТЗ', 'Подписанный акт приёма-передачи',
        ])
        make_ready(self.project, self.head)
        # домена, GitHub, доступов нет; задачи открыты — закрытию не мешает
        self.project.tasks.create(title='Открытая задача', status='new')
        self.assertTrue(closure.readiness(self.project).ready)
        closure.close_project(self.project, self.head)
        self.assertEqual(Project.objects.get(pk=self.project.pk).status, ProjectStatus.COMPLETED)

    def test_confirmations_are_head_only_with_history(self):
        with self.assertRaises(PermissionDenied):
            closure.set_confirmation(self.project, 'dev', True, self.pm_user)
        closure.set_confirmation(self.project, 'dev', True, self.head)
        self.project.refresh_from_db()
        self.assertEqual(self.project.dev_completed_by, self.head)
        self.assertIsNotNone(self.project.dev_completed_at)
        closure.set_confirmation(self.project, 'dev', False, self.head)
        self.project.refresh_from_db()
        self.assertIsNone(self.project.dev_completed_at)
        self.assertEqual(ProjectStatusHistory.objects.filter(project=self.project, field='Разработка завершена').count(), 2)

    def test_only_head_closes(self):
        make_ready(self.project, self.head)
        with self.assertRaises(PermissionDenied):
            closure.close_project(self.project, self.pm_user)
        self.client.force_login(self.pm_user)
        self.client.post(reverse('projects:complete', args=[self.project.pk]))
        self.client.post(reverse('projects:closure_confirm', args=[self.project.pk, 'dev']), {'value': '0'})
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, ProjectStatus.ACTIVE)
        self.assertIsNotNone(self.project.dev_completed_at)

    def test_not_ready_is_refused_with_reasons(self):
        upload_required(self.project, self.head)
        with self.assertRaises(closure.ClosureError) as ctx:
            closure.close_project(self.project, self.head)
        self.assertIn('Проект принят заказчиком', ' '.join(ctx.exception.messages))

    def test_recheck_inside_transaction(self):
        """Готовность проверяется заново: устаревший объект в памяти не поможет."""
        make_ready(self.project, self.head)
        stale = Project.objects.get(pk=self.project.pk)
        Project.objects.filter(pk=self.project.pk).update(client_accepted_at=None)
        with self.assertRaises(closure.ClosureError):
            closure.close_project(stale, self.head)

    def test_double_close_is_refused_and_history_written_once(self):
        make_ready(self.project, self.head)
        closure.close_project(self.project, self.head)
        with self.assertRaises(closure.ClosureError):
            closure.close_project(Project.objects.get(pk=self.project.pk), self.head)
        self.assertEqual(ProjectStatusHistory.objects.filter(
            project=self.project, field='Статус', new_value='Завершён').count(), 1)

    def test_close_sets_date_author_and_stops_recruitment(self):
        set_recruitment(self.project, True, self.head)
        make_ready(self.project, self.head)
        closure.close_project(self.project, self.head)
        self.project.refresh_from_db()
        self.assertEqual(self.project.closed_by, self.head)
        self.assertIsNotNone(self.project.actual_end_date)
        self.assertFalse(self.project.recruitment_open)
        novice = Intern.objects.create(full_name='Поздний', specialization=self.backend)
        self.assertTrue(check_assignment(novice, self.project, TeamRole.BACKEND)[0])

    def test_edit_form_admin_and_inline_cannot_complete(self):
        from apps.projects.admin import ProjectAdminForm

        for form_class in (ProjectForm, ProjectDetailsForm, ProjectAdminForm):
            with self.subTest(form=form_class.__name__):
                form = form_class(instance=self.project)
                self.assertNotIn(ProjectStatus.COMPLETED, [c[0] for c in form.fields['status'].choices])
        self.client.force_login(self.head)
        self.client.post(reverse('projects:section', args=[self.project.pk, 'details']),
                         {'status': 'completed', 'current_stage': self.project.current_stage, 'priority': self.project.priority})
        self.assertNotEqual(Project.objects.get(pk=self.project.pk).status, ProjectStatus.COMPLETED)

    def test_completed_project_status_is_locked(self):
        make_ready(self.project, self.head)
        closure.close_project(self.project, self.head)
        form = ProjectDetailsForm({'status': 'active', 'current_stage': 'completed', 'priority': 'medium'},
                                  instance=Project.objects.get(pk=self.project.pk))
        self.assertTrue(form.fields['status'].disabled)
        form.is_valid()
        self.assertEqual(form.cleaned_data.get('status'), ProjectStatus.COMPLETED)

    def test_other_projects_are_untouched(self):
        other = create_project(Project(name='Бета'))
        person = graduate('Двойной', self.backend)
        member(self.project, person)
        other_member = member(other, person)
        make_ready(self.project, self.head)
        closure.close_project(self.project, self.head)
        other_member.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(other_member.status, TeamMember.Status.ACTIVE)
        self.assertEqual(other.status, ProjectStatus.ACTIVE)

    def test_telegram_invites_only_after_commit(self):
        novice = Intern.objects.create(full_name='С ботом', specialization=self.backend, telegram_chat_id=77)
        member(self.project, novice)
        make_ready(self.project, self.head)
        with mock.patch('apps.graduate_bot.bot.bot.send_message') as send:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                closure.close_project(self.project, self.head)
            send.assert_not_called()
            for callback in callbacks:
                callback()
            send.assert_called_once()


class GraduationOnCloseTests(TestCase):
    def setUp(self):
        doc_services.ensure_default_types()
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.project = create_project(Project(name='Выпуск'))

    def close(self):
        make_ready(self.project, self.head)
        return closure.close_project(self.project, self.head)

    def test_active_new_interns_become_graduates(self):
        novice = Intern.objects.create(full_name='Новый Выпускник', specialization=self.backend, status=InternStatus.ACTIVE)
        member(self.project, novice)
        result = self.close()
        self.assertEqual([g.pk for g in result.graduates], [novice.pk])
        self.assertIn(novice.pk, compute([novice.pk]).ids)
        novice.refresh_from_db()
        self.assertEqual(novice.graduate_status, GraduateStatus.PENDING)
        self.assertEqual(novice.status, InternStatus.READY)

    def test_existing_graduate_keeps_status_and_only_this_membership_ends(self):
        other = create_project(Project(name='Второй проект'))
        old = graduate('Старый Выпускник', self.backend)
        Intern.objects.filter(pk=old.pk).update(graduate_status='')
        mine, theirs = member(self.project, old), member(other, old)
        result = self.close()
        self.assertEqual(result.graduates, [])  # не «новый» выпускник
        mine.refresh_from_db()
        theirs.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual(mine.status, TeamMember.Status.LEFT)
        self.assertEqual(theirs.status, TeamMember.Status.ACTIVE)
        self.assertEqual(old.status, InternStatus.ACTIVE)  # всё ещё работает на втором
        self.assertEqual(old.graduate_status, '')
        self.assertIn(old.pk, compute([old.pk]).ids)

    def test_frozen_interns_are_not_graduated(self):
        frozen = Intern.objects.create(full_name='Замороженный', specialization=self.backend, status=InternStatus.PAUSED)
        membership = member(self.project, frozen)
        result = self.close()
        self.assertEqual(result.graduates, [])
        membership.refresh_from_db()
        self.assertEqual(membership.left_reason, TeamMember.LeftReason.FROZEN)
        self.assertNotIn(frozen.pk, compute([frozen.pk]).ids)
        frozen.refresh_from_db()
        self.assertEqual((frozen.status, frozen.graduate_status), (InternStatus.PAUSED, ''))

    def test_left_or_expelled_before_close_are_not_graduated(self):
        people = []
        for reason in (TeamMember.LeftReason.SELF, TeamMember.LeftReason.NOT_FIT):
            person = Intern.objects.create(full_name=f'Ушёл {reason}', specialization=self.backend)
            TeamMember.objects.create(project=self.project, intern=person, role=TeamRole.BACKEND,
                                      status=TeamMember.Status.LEFT, left_reason=reason, left_at=END)
            people.append(person.pk)
        self.close()
        self.assertFalse(compute(people).ids)

    def test_second_attempt_is_not_proof(self):
        person = Intern.objects.create(full_name='Вторая попытка', specialization=self.backend, internship_attempt=2)
        self.assertNotIn(person.pk, compute([person.pk]).ids)

    def test_leads_are_not_graduated_but_notified(self):
        from apps.notifications.models import Notification

        _, lead = portal('g-lead', User.Role.TEAM_LEAD, TeamRole.TEAM_LEAD, self.project)
        result = self.close()
        self.assertEqual(result.graduates, [])
        self.assertTrue(Notification.objects.filter(intern=lead, title__endswith='завершён').exists())

    def test_release_is_idempotent(self):
        from apps.projects.services import release_team

        novice = Intern.objects.create(full_name='Однажды', specialization=self.backend)
        membership = member(self.project, novice)
        self.close()
        membership.refresh_from_db()
        left_at = membership.left_at
        self.assertEqual(release_team(Project.objects.get(pk=self.project.pk)), [])
        membership.refresh_from_db()
        self.assertEqual(membership.left_at, left_at)
        self.assertEqual(TeamMember.objects.filter(project=self.project).count(), 1)


class ClosureUiTests(TestCase):
    def setUp(self):
        doc_services.ensure_default_types()
        self.head = head()
        self.project = create_project(Project(name='Интерфейс'))
        self.backend = Specialization.objects.create(name='Backend')

    def test_checklist_shows_what_is_missing(self):
        self.client.force_login(self.head)
        response = self.client.get(self.project.get_absolute_url())
        self.assertContains(response, 'Готовность к закрытию')
        self.assertContains(response, '0 из 7')
        self.assertContains(response, 'Подтвердить')
        self.assertContains(response, 'Справочно')

    def test_ready_project_offers_confirmation_modal(self):
        novice = Intern.objects.create(full_name='Будущий Выпускник', specialization=self.backend)
        member(self.project, novice)
        make_ready(self.project, self.head)
        self.client.force_login(self.head)
        response = self.client.get(self.project.get_absolute_url())
        self.assertContains(response, 'data-gp-open="closure-confirm"')
        self.assertContains(response, 'Станут выпускниками')
        self.assertContains(response, 'Будущий Выпускник')
        self.assertContains(response, 'необратимо')

    def test_after_close_card_shows_result(self):
        novice = Intern.objects.create(full_name='Свежий Выпускник', specialization=self.backend)
        member(self.project, novice)
        make_ready(self.project, self.head)
        self.client.force_login(self.head)
        response = self.client.post(reverse('projects:complete', args=[self.project.pk]), follow=True)
        self.assertContains(response, 'Проект закрыт')
        self.assertContains(response, 'Свежий Выпускник')
        self.assertNotContains(response, 'data-gp-open="closure-confirm"')
        self.assertIn(self.project, Project.objects.filter(status=ProjectStatus.COMPLETED))
        listing = self.client.get(reverse('projects:list_completed'))
        self.assertContains(listing, 'Интерфейс')

    def test_pm_sees_no_close_button(self):
        pm, _ = portal('ui-pm', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.project)
        make_ready(self.project, self.head)
        self.client.force_login(pm)
        response = self.client.get(reverse('pm_portal:project_detail', args=[self.project.pk]))
        self.assertNotContains(response, 'Завершить проект')
        self.assertNotEqual(self.client.get(self.project.get_absolute_url()).status_code, 200)

    @override_settings(TELEGRAM_OUTBOUND_MODE='off')
    def test_brief_link_tab_highlight(self):
        doc_services.issue_brief_link(self.project)
        self.client.force_login(self.head)
        response = self.client.get(f'{self.project.get_absolute_url()}?tab=documents&brief=new')
        self.assertContains(response, 'gp-copyline--fresh')
        self.assertTrue(ProjectBriefLink.objects.filter(project=self.project, is_active=True).exists())

"""Этап 10A: сквозная безопасность ролей — прямые URL и POST, сервисы без
views, закрытые проекты и набор, старые сессии, публичные формы."""
import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.interns import freeze
from apps.interns.models import Intern, InternStatus
from apps.projects.fixtures_closure import head
from apps.projects.models import Project, ProjectStatus
from apps.projects.services import create_project
from apps.tasks import workflow
from apps.teams import staff
from apps.teams.models import TeamMember, TeamRole
from apps.training.models import Specialization
from apps.video import services as video

UserModel = get_user_model()
HEAD_PAGES = ('dashboard', 'projects:list', 'interns:list', 'interns:graduates', 'teams:lead_list',
              'teams:pm_list', 'reserve:board', 'weekly_reports:board', 'video:list', 'tgbot:reconcile',
              'notifications:list', 'documents:list', 'tasks:list')


class Base(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.frontend = Specialization.objects.create(name='Frontend')
        self.project = create_project(Project(name='Свой', recruitment_open=True))
        self.other = create_project(Project(name='Секретный чужой', recruitment_open=True))
        self.pm_user = UserModel.objects.create_user(username='s-pm', password='x', role='pm')
        self.pm = Intern.objects.create(full_name='ПМ Безопасности', user=self.pm_user)
        TeamMember.objects.create(project=self.project, intern=self.pm, role=TeamRole.PROJECT_MANAGER, status='active')
        self.lead_user = UserModel.objects.create_user(username='s-lead', password='x', role='team_lead')
        self.lead = Intern.objects.create(full_name='Тимлид Безопасности', user=self.lead_user,
                                          specialization=self.backend, position='lead', staff_role='lead',
                                          staff_status='active')
        staff.assign_lead(self.project, self.lead, self.head)
        self.intern = Intern.objects.create(full_name='Стажёр Безопасности', specialization=self.backend,
                                            status=InternStatus.ACTIVE)
        TeamMember.objects.create(project=self.project, intern=self.intern, role=TeamRole.BACKEND, status='active')


class PortalBoundaryTests(Base):
    def test_pm_and_lead_never_reach_head_pages(self):
        for user, home in ((self.pm_user, 'pm_portal:dashboard'), (self.lead_user, 'lead_portal:dashboard')):
            self.client.force_login(user)
            for name in HEAD_PAGES:
                with self.subTest(user=user.username, page=name):
                    response = self.client.get(reverse(name))
                    self.assertEqual(response.status_code, 302)
                    self.assertNotEqual(response['Location'], reverse(name))

    def test_direct_post_to_head_actions_does_nothing(self):
        self.client.force_login(self.pm_user)
        self.client.post(reverse('teams:dismiss', args=[self.lead.pk]), {'reason': 'захват'})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.staff_status, 'active')
        self.client.force_login(self.lead_user)
        self.client.post(reverse('teams:pm_add'), {'project': self.project.pk, 'intern': self.lead.pk})
        self.assertFalse(TeamMember.objects.filter(intern=self.lead, role=TeamRole.PROJECT_MANAGER).exists())

    def test_intern_account_has_no_internal_access(self):
        user = UserModel.objects.create_user(username='s-intern', password='x', role='intern')
        self.client.force_login(user)
        for name in HEAD_PAGES[:6] + ('pm_portal:dashboard', 'lead_portal:dashboard'):
            with self.subTest(page=name):
                self.assertNotEqual(self.client.get(reverse(name)).status_code, 200)

    def test_foreign_project_is_404_for_portals(self):
        self.client.force_login(self.lead_user)
        self.assertEqual(self.client.get(reverse('lead_portal:project_detail', args=[self.other.pk])).status_code, 404)
        self.client.force_login(self.pm_user)
        self.assertEqual(self.client.get(reverse('pm_portal:project_detail', args=[self.other.pk])).status_code, 404)
        task = workflow.create_task(self.other, self.head, title='Секретная задача')
        self.assertEqual(self.client.get(reverse('pm_portal:task_detail', args=[self.other.pk, task.pk])).status_code,
                         404)

    def test_old_session_dies_after_dismissal_and_studio(self):
        self.client.force_login(self.lead_user)
        staff.dismiss(self.lead, self.head, 'конец договора')
        self.assertEqual(self.client.get(reverse('lead_portal:dashboard')).status_code, 302)
        response = self.client.get(reverse('lead_portal:project_detail', args=[self.project.pk]))
        self.assertTrue(response['Location'].startswith(reverse('login')))


class ServiceLevelTests(Base):
    def close(self, project):
        Project.objects.filter(pk=project.pk).update(status=ProjectStatus.COMPLETED,
                                                     actual_end_date=timezone.localdate())
        project.refresh_from_db()

    def test_closed_project_accepts_no_new_assignments(self):
        new_user = UserModel.objects.create_user(username='s-lead2', password='x', role='team_lead')
        new_lead = Intern.objects.create(full_name='Новый Тимлид', user=new_user, specialization=self.backend,
                                         position='lead', staff_role='lead', staff_status='active')
        pm2 = Intern.objects.create(full_name='ПМ Стажёр', specialization=Specialization.objects.create(name='PM'))
        task = workflow.create_task(self.project, self.head, title='До закрытия', direction='backend')
        self.close(self.project)
        with self.assertRaises(staff.StaffError):
            staff.assign_lead(self.project, new_lead, self.head)
        with self.assertRaises(staff.StaffError):
            staff.assign_pm(self.project, pm2, self.head)
        with self.assertRaises(workflow.TaskError):
            workflow.assign(task, self.intern, self.lead_user)
        with self.assertRaises(video.MeetingError):
            video.create_meeting(self.lead_user, self.project, kind='required', topic='x', direction='backend',
                                 starts_at=timezone.now() + datetime.timedelta(hours=1), intern_ids=[self.intern.pk])

    def test_closed_recruitment_blocks_choice_everywhere(self):
        returner = Intern.objects.create(full_name='Вернувшийся', specialization=self.backend,
                                         status=InternStatus.ACTIVE)
        req = freeze.request_freeze(returner, 'пауза')
        freeze.approve(req, self.head)
        freeze.approve(freeze.request_return(Intern.objects.get(pk=returner.pk), 'готов'), self.head)
        returner.refresh_from_db()
        Project.objects.filter(pk=self.project.pk).update(recruitment_open=False)
        self.project.refresh_from_db()
        self.assertNotIn(self.project, freeze.available_projects(returner))
        with self.assertRaises(freeze.FreezeError):
            freeze.choose_project(returner, self.project)  # прямой вызов сервиса (как из бота)

    def test_services_check_roles_without_views(self):
        task = workflow.create_task(self.other, self.head, title='Чужая')
        with self.assertRaises(PermissionDenied):
            workflow.set_status(task, 'in_progress', self.pm_user)
        from apps.reserve import employment
        from apps.reserve.models import ReserveCandidate

        candidate = ReserveCandidate.objects.create(full_name='Кандидат', status='reserve')
        for user in (self.pm_user, self.lead_user):
            with self.assertRaises(PermissionDenied):
                employment.confirm_employment(candidate, user)

    def test_public_forms_reveal_nothing_internal(self):
        for url in (reverse('new_project_brief'), reverse('reserve_external_apply')):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                for secret in ('Секретный чужой', 'Стажёр Безопасности', 'Тимлид Безопасности'):
                    self.assertNotContains(response, secret)


class TaskFilesTests(Base):
    def test_submission_screenshot_visible_to_project_staff_only(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        task = workflow.create_task(self.project, self.head, title='Со скриншотом', direction='backend')
        workflow.assign(task, self.intern, self.head)
        shot = SimpleUploadedFile('s.png', b'\x89PNG\r\n\x1a\nx', content_type='image/png')
        submission = workflow.submit_work(task.pk, self.intern, screenshot=shot)
        url = submission.screenshot.url
        for user in (self.lead_user, self.pm_user, self.head):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 200, user.username)
        stranger = UserModel.objects.create_user(username='s-other-lead', password='x', role='team_lead')
        Intern.objects.create(full_name='Чужой Тимлид', user=stranger, specialization=self.backend)
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

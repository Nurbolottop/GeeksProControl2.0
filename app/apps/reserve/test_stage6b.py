"""Этап 6B: кадровый резерв и подтверждение трудоустройства в Geeks Studio."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.interns.forms import InternForm
from apps.interns.models import Intern, InternStatus, StaffRole, StaffStatus
from apps.notifications.models import Notification
from apps.projects.fixtures_closure import head
from apps.projects.models import Project
from apps.projects.services import create_project
from apps.reserve import employment, services
from apps.reserve.forms import ReserveCandidateForm, StatusChangeForm
from apps.reserve.models import CandidateStatus, ReserveCandidate, StudioEmployment
from apps.tasks.models import Task
from apps.teams import staff
from apps.teams.models import TeamMember, TeamRole
from apps.training.models import Specialization

UserModel = get_user_model()


class ReserveAndEmploymentTests(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.project = create_project(Project(name='Студийный', recruitment_open=True))
        user = UserModel.objects.create_user(username='r-lead', password='x', role=User.Role.TEAM_LEAD)
        self.lead = Intern.objects.create(full_name='Резервный Тимлид', user=user, specialization=self.backend,
                                          position='lead', staff_role=StaffRole.LEAD, staff_status=StaffStatus.ACTIVE,
                                          phone='0700 123 456')
        staff.assign_lead(self.project, self.lead, self.head)
        self.intern = Intern.objects.create(full_name='Стажёр Бэка', specialization=self.backend, status=InternStatus.ACTIVE)
        TeamMember.objects.create(project=self.project, intern=self.intern, role=TeamRole.BACKEND, status='active')

    def test_reserve_does_not_revoke_rights(self):
        candidate = employment.add_to_reserve(self.lead, self.head)
        self.assertEqual(candidate.status, CandidateStatus.RESERVE)
        self.lead.refresh_from_db()
        self.assertTrue(self.lead.user.is_active)
        self.assertEqual(self.lead.staff_status, StaffStatus.ACTIVE)
        self.assertTrue(TeamMember.objects.filter(intern=self.lead, role='team_lead', status='active').exists())
        self.client.force_login(self.lead.user)
        self.assertEqual(self.client.get(reverse('lead_portal:project_detail', args=[self.project.pk])).status_code, 200)
        self.assertFalse(Intern.objects.get(pk=self.lead.pk).in_resume_bank)  # банк резюме — отдельно
        employment.remove_from_reserve(candidate, self.head)
        candidate.refresh_from_db()
        self.assertEqual(candidate.status, CandidateStatus.INACTIVE)
        self.assertGreaterEqual(candidate.events.count(), 2)

    def test_employed_only_by_confirmation(self):
        candidate = employment.add_to_reserve(self.lead, self.head)
        self.assertNotIn(CandidateStatus.EMPLOYED, dict(StatusChangeForm(current=candidate.status).fields['status'].choices))
        with self.assertRaises(ValueError):
            services.change_status(candidate, CandidateStatus.EMPLOYED, user=self.head)
        form = ReserveCandidateForm({'full_name': candidate.full_name, 'status': CandidateStatus.EMPLOYED,
                                     'work_format': 'any', 'employment_type': 'any'}, instance=candidate)
        self.assertFalse(form.is_valid())
        self.assertIn('status', form.errors)
        intern_form = InternForm(instance=self.intern)
        self.assertNotIn('employed', [c[0] for c in intern_form.fields['status'].choices])
        for user in (self.lead.user, UserModel.objects.create_user(username='r-pm', password='x', role='pm')):
            with self.assertRaises(PermissionDenied):
                employment.confirm_employment(candidate, user)
            self.client.force_login(user)
            self.assertNotEqual(self.client.post(reverse('reserve:confirm_employment', args=[candidate.pk])).status_code, 200)
        self.assertFalse(StudioEmployment.objects.exists())

    def test_confirm_employment_of_lead(self):
        task = Task.objects.create(project=self.project, title='Сделано тимлидом', status='done', assignee=self.lead.user)
        candidate = employment.add_to_reserve(self.lead, self.head)
        self.client.force_login(self.lead.user)
        with self.captureOnCommitCallbacks(execute=True):
            employment.confirm_employment(candidate, self.head, position='Backend developer', comment='оффер принят')
        candidate.refresh_from_db()
        self.lead.refresh_from_db()
        self.assertEqual(candidate.status, CandidateStatus.EMPLOYED)
        self.assertEqual((self.lead.staff_status, self.lead.status), (StaffStatus.STUDIO, InternStatus.EMPLOYED))
        self.assertFalse(self.lead.user.is_active)
        self.assertEqual(set(TeamMember.objects.filter(intern=self.lead).values_list('left_reason', flat=True)),
                         {TeamMember.LeftReason.EMPLOYED_STUDIO})
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())
        self.assertTrue(Notification.objects.filter(title__startswith='Нужен тимлид: «Студийный» · Backend').exists())
        response = self.client.get(reverse('lead_portal:project_detail', args=[self.project.pk]))
        self.assertEqual(response.status_code, 302)  # старая сессия не работает
        with self.assertRaises(employment.EmploymentError):
            employment.confirm_employment(candidate, self.head)
        self.client.force_login(self.head)
        self.client.post(reverse('reserve:confirm_employment', args=[candidate.pk]))  # повторный POST
        self.assertEqual(StudioEmployment.objects.count(), 1)
        page = self.client.get(reverse('reserve:hr_detail', args=[candidate.pk]))
        self.assertContains(page, 'Трудоустройство в Geeks Studio подтверждено')
        self.assertNotContains(page, 'gp-legacy')

    def test_external_candidate_employment_creates_nothing_extra(self):
        candidate = ReserveCandidate.objects.create(full_name='Внешний Кандидат', phone='0555 000 111',
                                                    status=CandidateStatus.RESERVE)
        users_before = UserModel.objects.count()
        employment.confirm_employment(candidate, self.head)
        self.assertEqual(UserModel.objects.count(), users_before)
        self.assertFalse(Intern.objects.filter(full_name='Внешний Кандидат').exists())
        self.assertEqual(StudioEmployment.objects.get().intern, None)

    def test_board_pages(self):
        employment.add_to_reserve(self.lead, self.head)
        self.client.force_login(self.head)
        board = self.client.get(reverse('reserve:board'), {'kind': 'lead'})
        self.assertContains(board, 'Резервный Тимлид')
        self.assertNotContains(board, 'gp-legacy')
        self.assertEqual(self.client.get(reverse('reserve:board'), {'kind': 'external'}).context['page'].paginator.count, 0)
        self.client.force_login(self.lead.user)
        self.assertNotEqual(self.client.get(reverse('reserve:board')).status_code, 200)


class ExternalFormTests(TestCase):
    URL = 'reserve_external_apply'

    def setUp(self):
        cache.clear()
        self.backend = Specialization.objects.create(name='Backend')
        self.namesake = Intern.objects.create(full_name='Однофамилец Кандидат', phone='0777 999 888')

    def data(self, **extra):
        base = {'full_name': 'Однофамилец Кандидат', 'phone': '+996 700 55-66-77', 'telegram': '@cand',
                'specialization': self.backend.pk, 'work_experience': '2 года Django', 'consent_given': 'on'}
        base.update(extra)
        return base

    def test_submission_is_review_without_link_or_account(self):
        users_before = UserModel.objects.count()
        response = self.client.post(reverse(self.URL), self.data())
        self.assertEqual(response.status_code, 200)
        candidate = ReserveCandidate.objects.get()
        self.assertEqual((candidate.status, candidate.intern), (CandidateStatus.REVIEW, None))  # тёзка не связан
        self.assertEqual(UserModel.objects.count(), users_before)

    def test_duplicate_honeypot_rate_and_file(self):
        self.client.post(reverse(self.URL), self.data())
        self.client_class().post(reverse(self.URL), self.data(phone='0700556677'))  # тот же телефон
        self.assertEqual(ReserveCandidate.objects.count(), 1)
        self.client_class().post(reverse(self.URL), self.data(phone='0700111000', website='spam'))
        self.assertEqual(ReserveCandidate.objects.count(), 1)
        bad = SimpleUploadedFile('cv.exe', b'MZ', content_type='application/octet-stream')
        response = self.client_class().post(reverse(self.URL), {**self.data(phone='0700222000'), 'resume_file': bad})
        self.assertContains(response, 'запрещены')
        cache.clear()
        for i in range(6):
            response = self.client_class().post(reverse(self.URL), self.data(phone=f'070033300{i}'))
        self.assertEqual(response.status_code, 429)

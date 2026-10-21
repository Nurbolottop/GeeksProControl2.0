"""Этап 6A: управление ПМ и тимлидами, повышение стажёра, увольнение."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.dashboard.graduation import compute
from apps.interns import discipline, internships
from apps.interns.models import Intern, InternStatus, InternWarning, StaffRole, StaffStatus
from apps.notifications.models import Notification
from apps.projects.fixtures_closure import head
from apps.projects.models import Project
from apps.projects.services import create_project
from apps.tasks.models import Task
from apps.teams import staff
from apps.teams.models import TeamMember, TeamRole
from apps.teams.rules import check_assignment
from apps.training.models import Specialization

UserModel = get_user_model()


def person(name, role=None, spec=None, **extra):
    user = UserModel.objects.create_user(username=name, password='x', role=role) if role else None
    return Intern.objects.create(full_name=name, user=user, specialization=spec, **extra)


def member(project, intern, role=TeamRole.BACKEND):
    return TeamMember.objects.create(project=project, intern=intern, role=role, status=TeamMember.Status.ACTIVE)


class PmManagementTests(TestCase):
    def setUp(self):
        self.head = head()
        self.pm_spec = Specialization.objects.create(name='PM')
        self.a = create_project(Project(name='Альфа', recruitment_open=True))
        self.b = create_project(Project(name='Бета', recruitment_open=True))
        self.pm = person('pm-staff', User.Role.PROJECT_MANAGER, staff_role=StaffRole.PM, staff_status=StaffStatus.ACTIVE)
        self.other_pm = person('pm-other', User.Role.PROJECT_MANAGER, staff_role=StaffRole.PM,
                               staff_status=StaffStatus.ACTIVE)

    def test_assign_and_scope(self):
        self.client.force_login(self.head)
        self.client.post(reverse('teams:pm_add'), {'project': self.a.pk, 'intern': self.pm.pk})
        self.client.post(reverse('teams:pm_add'), {'project': self.b.pk, 'intern': self.pm.pk})  # сотрудник — 2 проекта можно
        self.assertEqual(TeamMember.objects.filter(intern=self.pm, role='pm', status='active').count(), 2)
        staff.assign_pm(self.b, self.other_pm, self.head)
        self.client.force_login(self.pm.user)
        self.assertEqual(self.client.get(reverse('pm_portal:project_detail', args=[self.a.pk])).status_code, 200)
        self.client.force_login(self.other_pm.user)
        self.assertEqual(self.client.get(reverse('pm_portal:project_detail', args=[self.a.pk])).status_code, 404)
        # роль в User не даёт глобальных прав: разделы руководителя закрыты
        for url in (reverse('teams:pm_list'), reverse('projects:detail', args=[self.a.pk])):
            self.assertNotEqual(self.client.get(url).status_code, 200)
        response = self.client.post(reverse('teams:pm_add'), {'project': self.a.pk, 'intern': self.other_pm.pk})
        self.assertNotEqual(response.status_code, 200)
        self.assertFalse(TeamMember.objects.filter(project=self.a, intern=self.other_pm, status='active').exists())

    def test_remove_and_replace_keep_history(self):
        member_a, _ = staff.assign_pm(self.a, self.pm, self.head)
        from apps.projects.models import ProjectReport

        ProjectReport.objects.create(project=self.a, text='Отчёт ПМ', author=self.pm.user)
        new = staff.replace(member_a, self.other_pm, self.head)
        member_a.refresh_from_db()
        self.assertEqual((member_a.status, member_a.left_reason), ('left', TeamMember.LeftReason.REPLACED))
        self.assertEqual(new.intern, self.other_pm)
        self.assertTrue(ProjectReport.objects.filter(project=self.a, author=self.pm.user).exists())
        self.client.force_login(self.pm.user)
        self.assertEqual(self.client.get(reverse('pm_portal:project_detail', args=[self.a.pk])).status_code, 404)
        with self.assertRaises(staff.StaffError):
            staff.replace(member_a, self.pm, self.head)  # повторная операция по закрытому назначению
        staff.remove_assignment(new, self.head)
        with self.assertRaises(staff.StaffError):
            staff.remove_assignment(new, self.head)

    def test_pm_direction_intern_follows_rules_and_is_not_promotion(self):
        novice = person('pm-novice', spec=self.pm_spec, status=InternStatus.ACTIVE)
        staff.assign_pm(self.a, novice, self.head)
        with self.assertRaises(staff.StaffError):
            staff.assign_pm(self.b, novice, self.head)  # новичок — один проект
        novice.refresh_from_db()
        self.assertEqual((novice.position, novice.staff_status), ('intern', ''))
        self.assertTrue(check_assignment(novice, self.b, TeamRole.PROJECT_MANAGER)[0])


class LeadManagementTests(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.frontend = Specialization.objects.create(name='Frontend')
        self.a = create_project(Project(name='Альфа', recruitment_open=True))
        self.b = create_project(Project(name='Бета', recruitment_open=True))
        self.lead = person('lead-staff', User.Role.TEAM_LEAD, self.backend, position='lead',
                           staff_role=StaffRole.LEAD, staff_status=StaffStatus.ACTIVE)
        self.back_intern = person('Бэк Стажёр', spec=self.backend, status=InternStatus.ACTIVE)
        self.front_intern = person('Фронт Стажёр', spec=self.frontend, status=InternStatus.ACTIVE)
        member(self.a, self.back_intern)
        member(self.a, self.front_intern, TeamRole.FRONTEND)

    def test_one_lead_several_projects_and_direction_isolation(self):
        staff.assign_lead(self.a, self.lead, self.head)
        staff.assign_lead(self.b, self.lead, self.head)
        _member, made = staff.assign_lead(self.b, self.lead, self.head)
        self.assertFalse(made)  # повторное назначение — без дубля
        self.assertEqual(TeamMember.objects.filter(intern=self.lead, role='team_lead', status='active').count(), 2)
        self.client.force_login(self.lead.user)
        url = reverse('lead_portal:intern_detail', args=[self.a.pk, self.back_intern.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(reverse('lead_portal:intern_detail',
                                                 args=[self.a.pk, self.front_intern.pk])).status_code, 404)

    def test_removal_revokes_project_rights(self):
        m, _ = staff.assign_lead(self.a, self.lead, self.head)
        staff.remove_assignment(m, self.head)
        self.client.force_login(self.lead.user)
        self.assertEqual(self.client.get(reverse('lead_portal:project_detail', args=[self.a.pk])).status_code, 404)
        self.assertTrue(Notification.objects.filter(title__startswith='Нужен тимлид: «Альфа» · Backend').exists())

    def test_dismissal(self):
        staff.assign_lead(self.a, self.lead, self.head)
        staff.assign_lead(self.b, self.lead, self.head)
        task = Task.objects.create(project=self.a, title='Задача тимлида', status='done', assignee=self.lead.user)
        self.client.force_login(self.lead.user)
        self.assertEqual(self.client.get(reverse('lead_portal:project_detail', args=[self.a.pk])).status_code, 200)
        with self.captureOnCommitCallbacks(execute=True):
            staff.dismiss(self.lead, self.head, 'по собственному желанию')
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.staff_status, StaffStatus.DISMISSED)
        self.assertFalse(self.lead.user.is_active)
        self.assertEqual(set(TeamMember.objects.filter(intern=self.lead).values_list('left_reason', flat=True)),
                         {TeamMember.LeftReason.DISMISSED})
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())
        # старая сессия больше не работает
        response = self.client.get(reverse('lead_portal:project_detail', args=[self.a.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)
        self.assertTrue(Notification.objects.filter(title__startswith='Нужен тимлид: «Альфа» · Backend').exists())
        with self.assertRaises(staff.StaffError):
            staff.dismiss(Intern.objects.get(pk=self.lead.pk), self.head, 'ещё раз')

    def test_head_only_and_direct_posts(self):
        m, _ = staff.assign_lead(self.a, self.lead, self.head)
        with self.assertRaises(PermissionDenied):
            staff.dismiss(self.lead, self.lead.user, 'сам себя')
        self.client.force_login(self.lead.user)
        for url, data in ((reverse('teams:dismiss', args=[self.lead.pk]), {'reason': 'x'}),
                          (reverse('teams:lead_remove', args=[m.pk]), {}),
                          (reverse('teams:replace', args=[m.pk]), {'intern': self.back_intern.pk}),
                          (reverse('interns:promote_lead', args=[self.back_intern.pk]), {})):
            with self.subTest(url=url):
                self.assertNotEqual(self.client.post(url, data).status_code, 200)
        m.refresh_from_db()
        self.assertEqual(m.status, 'active')
        self.assertEqual(Intern.objects.get(pk=self.back_intern.pk).position, 'intern')

    def test_pages_render_in_new_ui(self):
        staff.assign_lead(self.a, self.lead, self.head)
        self.client.force_login(self.head)
        for url in (reverse('teams:lead_list'), reverse('teams:pm_list'), reverse('teams:staff_detail', args=[self.lead.pk])):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'gp-legacy')
        self.assertContains(self.client.get(reverse('teams:lead_list'), {'q': 'lead-staff'}), 'Альфа')


class PromotionTests(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.a = create_project(Project(name='Альфа', recruitment_open=True))
        self.b = create_project(Project(name='Бета', recruitment_open=True))
        self.user = UserModel.objects.create_user(username='intern-up', password='x', role=User.Role.INTERN)
        self.intern = Intern.objects.create(full_name='Растущий', specialization=self.backend,
                                            status=InternStatus.ACTIVE, user=self.user)
        self.m = member(self.a, self.intern)
        self.task = Task.objects.create(project=self.a, title='В работе', status='in_progress', assignee=self.user)
        discipline.issue_warning(self.intern, self.a, 'старое', self.head)

    def test_promotion_is_graduation_and_ends_internship(self):
        period = internships.current(self.intern)
        self.assertTrue(staff.promote_to_lead(self.intern, self.head))
        self.intern.refresh_from_db()
        period.refresh_from_db()
        self.m.refresh_from_db()
        self.assertEqual((self.intern.position, self.intern.staff_status, self.intern.staff_role),
                         ('lead', StaffStatus.ACTIVE, StaffRole.LEAD))
        self.assertEqual(period.end_reason, 'promoted')
        self.assertIsNotNone(period.graduated_on)
        self.assertEqual((self.m.status, self.m.left_reason), ('left', TeamMember.LeftReason.PROMOTED))
        self.assertEqual(Task.objects.get(pk=self.task.pk).status, 'in_progress')  # история задач цела
        self.assertTrue(InternWarning.objects.filter(intern=self.intern).exists())
        self.assertIn(self.intern.pk, compute([self.intern.pk]).ids)
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, 'team_lead')
        self.assertEqual(Intern.objects.filter(full_name='Растущий').count(), 1)
        self.assertFalse(staff.promote_to_lead(Intern.objects.get(pk=self.intern.pk), self.head))  # повторно — нет
        # права тимлида — только после назначения на проект
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('lead_portal:project_detail', args=[self.b.pk])).status_code, 404)
        staff.assign_lead(self.b, Intern.objects.get(pk=self.intern.pk), self.head)
        self.assertEqual(self.client.get(reverse('lead_portal:project_detail', args=[self.b.pk])).status_code, 200)

    def test_existing_graduate_is_not_graduated_again(self):
        from apps.projects import closure
        from apps.projects.fixtures_closure import make_ready

        make_ready(self.a, self.head)
        closure.close_project(self.a, self.head)
        period = self.intern.internships.get()
        first = period.graduated_on
        member(self.b, Intern.objects.get(pk=self.intern.pk))
        staff.promote_to_lead(Intern.objects.get(pk=self.intern.pk), self.head)
        period.refresh_from_db()
        self.assertEqual(period.graduated_on, first)
        self.assertEqual(self.intern.internships.count(), 1)

    def test_expelled_cannot_be_promoted(self):
        from django.db import transaction

        with transaction.atomic():
            discipline.expel(Intern.objects.select_for_update().get(pk=self.intern.pk), self.head, 'решение')
        with self.assertRaises(staff.StaffError):
            staff.promote_to_lead(Intern.objects.get(pk=self.intern.pk), self.head)

    def test_promote_view(self):
        self.client.force_login(self.head)
        self.client.post(reverse('interns:promote_lead', args=[self.intern.pk]))
        self.client.post(reverse('interns:promote_lead', args=[self.intern.pk]))  # повторный POST
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.position, 'lead')
        from apps.audit.models import AuditLog

        self.assertEqual(AuditLog.objects.filter(object_id=str(self.intern.pk), action='Повышен(а) до тимлида').count(), 1)

"""UI-03A: модуль «Проекты» — список, карточка, этапы, команда, набор, права."""
import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.interns.models import Intern, InternStatus
from apps.projects import card, services
from apps.projects.models import Project, ProjectStage, ProjectStageKey, ProjectStatus, ProjectStatusHistory
from apps.teams.models import TeamMember, TeamRole
from apps.teams.rules import check_assignment
from apps.training.models import Specialization

UserModel = get_user_model()
END = datetime.date(2026, 9, 30)


def head_user(name='u3-head'):
    return UserModel.objects.create_user(username=name, password='x', role=User.Role.HEAD)


def portal_user(name, role, team_role, *projects):
    user = UserModel.objects.create_user(username=name, password='x', role=role)
    intern = Intern.objects.create(full_name=name, user=user)
    for project in projects:
        TeamMember.objects.create(project=project, intern=intern, role=team_role, status=TeamMember.Status.ACTIVE)
    return user, intern


def graduate(name, spec=None):
    """Старый стажёр: был активен при официальном закрытии проекта."""
    done = Project.objects.create(name=f'Сданный {name}', status=ProjectStatus.COMPLETED, actual_end_date=END)
    person = Intern.objects.create(full_name=name, specialization=spec, status=InternStatus.ACTIVE)
    TeamMember.objects.create(project=done, intern=person, role=TeamRole.BACKEND, status=TeamMember.Status.LEFT,
                              left_reason=TeamMember.LeftReason.PROJECT_ENDED, left_at=END)
    return person


class ProjectListTests(TestCase):
    def setUp(self):
        self.head = head_user()
        self.client.force_login(self.head)
        self.alpha = Project.objects.create(name='Альфа', progress=80, start_date=datetime.date(2026, 1, 5))
        self.beta = Project.objects.create(name='Бета', progress=20, start_date=datetime.date(2026, 3, 1))
        self.gamma = Project.objects.create(name='Гамма', status=ProjectStatus.PAUSED, progress=50)
        stage = ProjectStage.objects.create(project=self.beta, key=ProjectStageKey.TESTING)
        Project.objects.filter(pk=self.beta.pk).update(primary_stage=stage)
        _, self.pm_card = portal_user('u3-pm', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.alpha)
        _, self.lead_card = portal_user('u3-lead', User.Role.TEAM_LEAD, TeamRole.TEAM_LEAD, self.gamma)

    def names(self, **params):
        response = self.client.get(reverse('projects:list'), params)
        return [p.name for p in response.context['page_obj'].object_list]

    def test_filters(self):
        self.assertEqual(self.names(q='аль'), ['Альфа'])
        self.assertEqual(self.names(status='paused'), ['Гамма'])
        self.assertEqual(self.names(primary_stage='testing'), ['Бета'])
        self.assertEqual(sorted(self.names(primary_stage='unset')), ['Альфа', 'Гамма'])
        self.assertEqual(self.names(pm=self.pm_card.pk), ['Альфа'])
        self.assertEqual(self.names(lead=self.lead_card.pk), ['Гамма'])

    def test_sorting(self):
        self.assertEqual(self.names(sort='-progress'), ['Альфа', 'Гамма', 'Бета'])
        self.assertEqual(self.names(sort='start')[:2], ['Альфа', 'Бета'])
        self.assertEqual(self.names(sort='bogus'), self.names())  # неизвестная сортировка — по умолчанию

    def test_members_counted_as_unique_people(self):
        person = Intern.objects.create(full_name='Двойной')
        TeamMember.objects.create(project=self.alpha, intern=person, role=TeamRole.BACKEND)
        TeamMember.objects.create(project=self.alpha, intern=person, role=TeamRole.QA)  # тот же человек
        response = self.client.get(reverse('projects:list'), {'q': 'Альфа'})
        self.assertEqual(response.context['page_obj'].object_list[0].members_count, 2)  # ПМ + Двойной

    def test_htmx_returns_only_table(self):
        response = self.client.get(reverse('projects:list'), {'sort': 'name'}, HTTP_HX_REQUEST='true', HTTP_HX_TARGET='project-list')
        self.assertNotContains(response, 'gp-shell')
        self.assertContains(response, 'gp-table')

    def test_pm_and_lead_see_only_their_projects(self):
        self.client.force_login(UserModel.objects.get(username='u3-pm'))
        response = self.client.get(reverse('pm_portal:dashboard'))
        self.assertEqual([p.name for p in response.context['page_obj'].object_list], ['Альфа'])
        self.assertNotContains(response, 'Бета')
        self.assertRedirects(self.client.get(reverse('projects:list')), reverse('pm_portal:dashboard'), fetch_redirect_response=False)
        self.client.force_login(UserModel.objects.get(username='u3-lead'))
        response = self.client.get(reverse('lead_portal:dashboard'))
        self.assertEqual([p.name for p in response.context['page_obj'].object_list], ['Гамма'])


class CardAccessTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Карточка')
        self.other = Project.objects.create(name='Чужая')

    def test_head_sees_all_tabs(self):
        self.client.force_login(head_user())
        for tab in ('overview', 'stages', 'team', 'tasks', 'documents', 'report', 'access', 'graphics', 'history'):
            with self.subTest(tab=tab):
                response = self.client.get(self.project.get_absolute_url(), {'tab': tab})
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'gp-legacy')  # доступы, графика, история — тоже 2.0

    def test_others_cannot_open_or_post(self):
        intern = UserModel.objects.create_user(username='u3-intern', password='x', role=User.Role.INTERN)
        pm, _ = portal_user('u3-pm2', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.other)
        lead, _ = portal_user('u3-lead2', User.Role.TEAM_LEAD, TeamRole.TEAM_LEAD, self.project)
        urls_get = [self.project.get_absolute_url()]
        urls_post = [reverse('projects:recruitment_set', args=[self.project.pk]),
                     reverse('projects:primary_stage_set', args=[self.project.pk])]
        self.client.force_login(intern)
        for url in urls_get + urls_post:
            self.assertEqual(self.client.post(url, {'open': '1'}).status_code, 403)
        for user, portal in ((pm, 'pm_portal:dashboard'), (lead, 'lead_portal:dashboard')):
            self.client.force_login(user)
            for url in urls_get:
                self.assertRedirects(self.client.get(url), reverse(portal), fetch_redirect_response=False)
            for url in urls_post:
                self.assertRedirects(self.client.post(url, {'open': '1'}), reverse(portal), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('lead_portal:project_detail', args=[self.other.pk])).status_code, 404)
        self.project.refresh_from_db()
        self.assertFalse(self.project.recruitment_open)


class OverviewTests(TestCase):
    def test_overview_shows_real_data_and_empty_states(self):
        backend = Specialization.objects.create(name='Backend')
        project = Project.objects.create(name='Обзорный', staging_url='https://stage.example.com')
        portal_user('u3-pm3', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, project)
        old = graduate('Старый Один', backend)
        TeamMember.objects.create(project=project, intern=old, role=TeamRole.BACKEND)
        new = Intern.objects.create(full_name='Новый Один', specialization=backend)
        TeamMember.objects.create(project=project, intern=new, role=TeamRole.BACKEND)
        self.client.force_login(head_user())
        response = self.client.get(project.get_absolute_url())
        data = response.context['overview']
        self.assertEqual((data['new'], data['old'], data['unique']), (1, 1, 3))
        self.assertEqual(data['links'], [('Staging', 'https://stage.example.com')])
        self.assertContains(response, 'Отчётов по проекту ещё нет')
        self.assertContains(response, 'не назначены')  # тимлидов нет
        self.assertContains(response, 'Этап не выбран')


class StagesTests(TestCase):
    def setUp(self):
        self.head = head_user()
        self.client.force_login(self.head)
        self.project = Project.objects.create(name='Этапы')
        keys = [ProjectStageKey.DESIGN, ProjectStageKey.BACKEND, ProjectStageKey.FRONTEND, ProjectStageKey.TESTING, ProjectStageKey.COMPLETED]
        self.stages = {k: ProjectStage.objects.create(project=self.project, key=k, order=i) for i, k in enumerate(keys)}

    def test_parallel_stages_and_equal_weight_progress(self):
        S = ProjectStage.Status
        for key, status in ((ProjectStageKey.DESIGN, S.DONE), (ProjectStageKey.BACKEND, S.IN_PROGRESS), (ProjectStageKey.FRONTEND, S.IN_PROGRESS)):
            ProjectStage.objects.filter(pk=self.stages[key].pk).update(status=status)
        data = card.stage_rows(self.project)
        self.assertEqual((data['done'], data['in_progress'], data['total'], data['progress']), (1, 2, 4, 25))  # «Завершён» не в счёт

    def test_choosing_stage_aligns_statuses_progress_and_history(self):
        services.set_primary_stage(self.project, self.stages[ProjectStageKey.BACKEND], self.head)
        self.project.refresh_from_db()
        status = {s.key: s.status for s in self.project.stages.all()}
        self.assertEqual(status[ProjectStageKey.DESIGN], 'done')
        self.assertEqual(status[ProjectStageKey.BACKEND], 'in_progress')
        self.assertEqual(status[ProjectStageKey.FRONTEND], 'not_started')
        self.assertEqual(self.project.current_stage, ProjectStageKey.BACKEND)
        self.assertTrue(ProjectStatusHistory.objects.filter(project=self.project, field='Этап', new_value='Backend').exists())
        response = self.client.get(self.project.get_absolute_url(), {'tab': 'stages'})
        self.assertContains(response, 'Этап: Backend')
        self.assertNotContains(response, 'основной')
        # «Продакшен» — всё сделано: готовность 100%
        production = self.project.stages.filter(key=ProjectStageKey.PRODUCTION).first()
        if production:
            services.set_primary_stage(self.project, production, self.head)
            self.project.refresh_from_db()
            self.assertEqual(self.project.progress, 100)

    def test_unconfirmed_stage_shows_warning_and_action(self):
        response = self.client.get(self.project.get_absolute_url(), {'tab': 'stages'})
        self.assertContains(response, 'Этап не выбран')
        self.assertContains(response, 'Сохранить этап')


class TeamRulesTests(TestCase):
    def setUp(self):
        self.backend = Specialization.objects.create(name='Backend')
        # Набор открыт: здесь проверяются правила состава, закрытый набор — в test_ui03b
        self.p1 = Project.objects.create(name='Первый', recruitment_open=True)
        self.p2 = Project.objects.create(name='Второй', recruitment_open=True)
        self.p3 = Project.objects.create(name='Третий', recruitment_open=True)
        self.head = head_user()
        self.client.force_login(self.head)

    def add(self, project, intern):
        return self.client.post(reverse('teams:member_add', args=[project.pk]), {'intern': intern.pk})

    def test_new_intern_only_one_project(self):
        novice = Intern.objects.create(full_name='Новичок', specialization=self.backend, status=InternStatus.ACTIVE)
        self.add(self.p1, novice)
        response = self.add(self.p2, novice)
        self.assertFalse(TeamMember.objects.filter(project=self.p2, intern=novice).exists())
        self.assertContains(response, 'вторая параллельная стажировка запрещена')

    def test_old_intern_max_two_projects(self):
        old = graduate('Старый', self.backend)
        self.add(self.p1, old)
        self.add(self.p2, old)
        self.assertEqual(TeamMember.objects.filter(intern=old, status='active').count(), 2)
        self.add(self.p3, old)
        self.assertFalse(TeamMember.objects.filter(project=self.p3, intern=old).exists())

    def test_four_old_per_direction_hard_limit_new_soft(self):
        for i in range(4):
            TeamMember.objects.create(project=self.p1, intern=graduate(f'Старый {i}', self.backend), role=TeamRole.BACKEND)
        fifth = graduate('Пятый Старый', self.backend)
        errors, _ = check_assignment(fifth, self.p1, TeamRole.BACKEND)
        self.assertTrue(errors)
        novice = Intern.objects.create(full_name='Новичок Пятый', specialization=self.backend)
        errors, warnings = check_assignment(novice, self.p1, TeamRole.BACKEND)
        self.assertEqual(errors, [])
        self.assertTrue(warnings)  # ориентир 4 — только предупреждение

    def test_readmitted_after_expulsion_counts_as_new(self):
        person = Intern.objects.create(full_name='Вернулся', specialization=self.backend, internship_attempt=2)
        TeamMember.objects.create(project=self.p1, intern=person, role=TeamRole.BACKEND)
        errors, _ = check_assignment(person, self.p2, TeamRole.BACKEND)
        self.assertTrue(errors)  # не выпускник — второй проект запрещён

    def test_form_link_join_respects_rules(self):
        from apps.interns.models import ProfileFormLink
        from apps.interns.services import join_project_from_form

        novice = Intern.objects.create(full_name='По Ссылке', specialization=self.backend)
        TeamMember.objects.create(project=self.p1, intern=novice, role=TeamRole.BACKEND)
        link = ProfileFormLink.objects.create(project=self.p2)
        self.assertIsNone(join_project_from_form(novice, link))
        self.assertFalse(TeamMember.objects.filter(project=self.p2, intern=novice).exists())

    def test_team_tab_filters_and_badges(self):
        old = graduate('Старый Т', self.backend)
        TeamMember.objects.create(project=self.p1, intern=old, role=TeamRole.BACKEND)
        novice = Intern.objects.create(full_name='Новый Т', specialization=self.backend)
        TeamMember.objects.create(project=self.p1, intern=novice, role=TeamRole.BACKEND)
        gone = Intern.objects.create(full_name='Вышел Т')
        TeamMember.objects.create(project=self.p1, intern=gone, role=TeamRole.QA, status=TeamMember.Status.LEFT)
        url = self.p1.get_absolute_url()
        response = self.client.get(url, {'tab': 'team', 'kind': 'old'})
        self.assertContains(response, 'Старый Т')
        self.assertNotContains(response, 'Новый Т')
        self.assertNotContains(self.client.get(url, {'tab': 'team'}), 'Вышел Т')
        self.assertContains(self.client.get(url, {'tab': 'team', 'left': '1'}), 'Вышел Т')


class RecruitmentTests(TestCase):
    def setUp(self):
        self.head = head_user()
        self.project = Project.objects.create(name='Набор')

    def test_open_close_reopen_with_history(self):
        self.client.force_login(self.head)
        url = reverse('projects:recruitment_set', args=[self.project.pk])
        self.assertFalse(self.project.recruitment_open)  # по умолчанию закрыт
        self.assertNotIn(self.project, services.projects_open_for_recruitment())
        self.client.post(url, {'open': '1'})
        self.project.refresh_from_db()
        self.assertTrue(self.project.recruitment_open)
        self.assertIn(self.project, services.projects_open_for_recruitment())
        self.client.post(url, {'open': '0'})
        self.client.post(url, {'open': '1'})
        self.project.refresh_from_db()
        self.assertTrue(self.project.recruitment_open)
        self.assertEqual(self.project.recruitment_changed_by, self.head)
        self.assertEqual(ProjectStatusHistory.objects.filter(project=self.project, field='Набор').count(), 3)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_closed_or_finished_projects_not_offered(self):
        services.set_recruitment(self.project, True, self.head)
        Project.objects.filter(pk=self.project.pk).update(status=ProjectStatus.COMPLETED)
        self.assertNotIn(self.project, services.projects_open_for_recruitment())

    def test_only_head_can_change(self):
        pm, _ = portal_user('u3-pm4', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.project)
        with self.assertRaises(PermissionDenied):
            services.set_recruitment(self.project, True, pm)


class AssignmentHistoryTests(TestCase):
    def test_replacing_pm_keeps_history(self):
        head = head_user()
        self.client.force_login(head)
        project = Project.objects.create(name='Смена ПМ')
        _, old_pm = portal_user('u3-oldpm', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, project)
        member = TeamMember.objects.get(project=project, intern=old_pm)
        self.client.post(reverse('teams:member_delete', args=[member.pk]))
        member.refresh_from_db()
        self.assertEqual(member.status, TeamMember.Status.LEFT)  # запись осталась
        self.assertIsNotNone(member.left_at)
        _, new_pm = portal_user('u3-newpm', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, project)
        self.assertEqual(TeamMember.objects.filter(project=project, role=TeamRole.PROJECT_MANAGER).count(), 2)
        response = self.client.get(project.get_absolute_url(), {'tab': 'team', 'left': '1'})
        self.assertContains(response, 'u3-oldpm')
        self.assertContains(response, 'вышел')

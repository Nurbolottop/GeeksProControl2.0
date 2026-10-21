"""UI-04A: модуль «Стажёры и выпускники» — один профиль, периоды
стажировок, история, выпускники, правила новых/старых, права, списки."""
import datetime
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import User
from apps.dashboard import metrics
from apps.dashboard.graduation import compute
from apps.interns import freeze, internships
from apps.interns.models import Intern, Internship, InternStatus, InternWarning
from apps.projects.fixtures_closure import head, make_ready
from apps.projects import closure
from apps.projects.models import Project, ProjectStatus
from apps.projects.services import create_project
from apps.teams.models import TeamMember, TeamRole
from apps.teams.rules import check_assignment
from apps.teams.services import save_membership
from apps.training.models import Specialization

UserModel = get_user_model()
END = datetime.date(2026, 9, 30)


def member(project, intern, role=TeamRole.BACKEND, **extra):
    return TeamMember.objects.create(project=project, intern=intern, role=role,
                                     status=TeamMember.Status.ACTIVE, **extra)


def graduate(name, spec, **extra):
    done = Project.objects.create(name=f'Сданный {name}', status=ProjectStatus.COMPLETED, actual_end_date=END)
    person = Intern.objects.create(full_name=name, specialization=spec, status=InternStatus.ACTIVE, **extra)
    TeamMember.objects.create(project=done, intern=person, role=TeamRole.BACKEND, status=TeamMember.Status.LEFT,
                              left_reason=TeamMember.LeftReason.PROJECT_ENDED, left_at=END)
    return person


def portal(name, role, team_role, *projects, spec=None):
    user = UserModel.objects.create_user(username=name, password='x', role=role)
    intern = Intern.objects.create(full_name=name, user=user, specialization=spec)
    for project in projects:
        member(project, intern, team_role)
    return user, intern


class ProfileAndPeriodsTests(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.a = create_project(Project(name='Альфа', recruitment_open=True))
        self.b = create_project(Project(name='Бета', recruitment_open=True))
        self.person = Intern.objects.create(full_name='Один Профиль', specialization=self.backend,
                                            status=InternStatus.ACTIVE, internship_start_date=datetime.date(2026, 9, 1))

    def test_join_opens_period_from_card_date(self):
        member(self.a, self.person, joined_at=datetime.date(2026, 9, 5))
        period = internships.current(self.person)
        self.assertEqual((period.number, period.started_on, period.source), (1, datetime.date(2026, 9, 1), 'card'))

    def test_freeze_and_return_keep_one_profile_and_period(self):
        member(self.a, self.person)
        period = internships.current(self.person)
        freeze.freeze_by_head(self.person, 'Отпуск', self.head)
        freeze.return_by_head(Intern.objects.get(pk=self.person.pk), 'Готов', self.head)
        person = Intern.objects.get(pk=self.person.pk)
        freeze.choose_project(person, self.b)
        self.assertEqual(Intern.objects.filter(full_name='Один Профиль').count(), 1)
        self.assertEqual(list(person.internships.all()), [period])  # тот же период
        self.assertTrue(internships.current(person).is_open)

    def test_sequential_internships_on_one_profile(self):
        member(self.a, self.person)
        first = internships.current(self.person)
        internships.close(first, Internship.EndReason.EXPELLED, note='три предупреждения')
        TeamMember.objects.filter(intern=self.person).update(status=TeamMember.Status.LEFT,
                                                             left_reason=TeamMember.LeftReason.NOT_FIT)
        Intern.objects.filter(pk=self.person.pk).update(internship_attempt=2)
        person = Intern.objects.get(pk=self.person.pk)
        member(self.b, person)
        second = internships.current(person)
        self.assertEqual(second.number, 2)
        self.assertEqual(person.internships.count(), 2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Internship.objects.create(intern=person, number=3)  # второй открытый период — нельзя

    def test_lead_membership_does_not_open_internship(self):
        lead = Intern.objects.create(full_name='Тимлид Сотрудник')
        member(self.a, lead, TeamRole.TEAM_LEAD)
        self.assertFalse(lead.internships.exists())

    def test_backfill_report_then_apply(self):
        legacy = Intern.objects.create(full_name='Старые Данные', status=InternStatus.EMPLOYED,
                                       internship_start_date=datetime.date(2025, 1, 10), internship_attempt=2)
        TeamMember.objects.filter(intern=legacy).delete()
        Internship.objects.filter(intern=legacy).delete()
        out = StringIO()
        call_command('internship_backfill', stdout=out)
        self.assertIn('Старые Данные', out.getvalue())
        self.assertFalse(legacy.internships.exists())
        call_command('internship_backfill', '--apply', stdout=StringIO())
        period = legacy.internships.get()
        self.assertEqual((period.number, period.started_on, period.end_reason, period.ended_on),
                         (2, datetime.date(2025, 1, 10), Internship.EndReason.EMPLOYED, None))


class RulesTests(TestCase):
    def setUp(self):
        self.backend = Specialization.objects.create(name='Backend')
        self.p = [create_project(Project(name=f'П{i}', recruitment_open=True)) for i in range(3)]

    def test_new_intern_only_one_project_even_under_race(self):
        from apps.teams.forms import TeamMemberForm

        novice = Intern.objects.create(full_name='Новичок', specialization=self.backend)
        form = TeamMemberForm({'intern': novice.pk}, role=TeamRole.BACKEND, project=self.p[1])
        self.assertTrue(form.is_valid(), form.errors)  # проверка формы прошла раньше…
        member(self.p[0], novice)                     # …а тем временем он вступил в другой проект
        pending = form.save(commit=False)
        pending.project = self.p[1]
        self.assertIn('вторая параллельная стажировка запрещена', save_membership(pending))
        self.assertFalse(TeamMember.objects.filter(project=self.p[1], intern=novice).exists())

    def test_graduate_in_two_projects_not_three(self):
        old = graduate('Старый', self.backend)
        member(self.p[0], old)
        self.assertEqual(check_assignment(old, self.p[1], TeamRole.BACKEND)[0], [])
        member(self.p[1], old)
        self.assertTrue(check_assignment(old, self.p[2], TeamRole.BACKEND)[0])

    def test_four_graduates_per_direction(self):
        for i in range(4):
            member(self.p[0], graduate(f'Старый {i}', self.backend))
        self.assertTrue(check_assignment(graduate('Пятый', self.backend), self.p[0], TeamRole.BACKEND)[0])
        for i in range(5):
            member(self.p[1], Intern.objects.create(full_name=f'Новый {i}', specialization=self.backend))
        errors, warnings = check_assignment(Intern.objects.create(full_name='Шестой новый', specialization=self.backend),
                                            self.p[1], TeamRole.BACKEND)
        self.assertEqual(errors, [])
        self.assertTrue(warnings)  # ориентир 4 для новых — только предупреждение


class HistoryAndGraduationTests(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.client.force_login(self.head)

    def test_history_kept_after_leaving_and_freeze(self):
        a = create_project(Project(name='Первый проект', recruitment_open=True))
        b = create_project(Project(name='Второй проект', recruitment_open=True))
        person = Intern.objects.create(full_name='С Историей', specialization=self.backend, status=InternStatus.ACTIVE)
        old_member = member(a, person)
        from apps.teams.services import leave_team

        leave_team(old_member, TeamMember.LeftReason.SELF)
        member(b, Intern.objects.get(pk=person.pk))
        freeze.freeze_by_head(Intern.objects.get(pk=person.pk), 'Сессия', self.head)
        response = self.client.get(person.get_absolute_url())
        self.assertContains(response, 'Первый проект')
        self.assertContains(response, 'Второй проект')
        self.assertContains(response, 'Стажировка заморожена')
        self.assertContains(response, 'Сам ушёл')
        titles = [e['title'] for e in response.context['timeline']]
        self.assertIn('Вышел(а) из «Первый проект»', titles)
        self.assertIn('Вышел(а) из «Второй проект»', titles)
        self.assertEqual(len(response.context['past_memberships']), 2)

    def test_graduate_status_kept_when_joining_new_project(self):
        project = create_project(Project(name='Выпускной', recruitment_open=True))
        person = Intern.objects.create(full_name='Будущий Выпускник', specialization=self.backend,
                                       status=InternStatus.ACTIVE)
        member(project, person)
        make_ready(project, self.head)
        closure.close_project(project, self.head)
        new = create_project(Project(name='Следующий', recruitment_open=True))
        member(new, Intern.objects.get(pk=person.pk))
        self.assertIn(person.pk, compute([person.pk]).ids)
        response = self.client.get(reverse('interns:graduates'))
        row = next(p for p in response.context['page'].object_list if p.pk == person.pk)
        self.assertEqual([m.project.name for m in row.ui_memberships], ['Следующий'])
        self.assertEqual([item[1] for item in row.ui_graduated_projects], ['Выпускной'])
        self.assertEqual(row.ui_activity[0], 'Участвует в проекте')

    def test_readmission_is_not_graduation(self):
        person = Intern.objects.create(full_name='Повторно Зачислен', internship_attempt=2, status=InternStatus.ACTIVE)
        self.assertNotIn(person.pk, compute().ids)
        response = self.client.get(reverse('interns:graduates'))
        self.assertNotContains(response, 'Повторно Зачислен')
        card = self.client.get(person.get_absolute_url())
        self.assertContains(card, 'Подтверждённого выпуска нет')
        self.assertContains(card, 'Нужна сверка')

    def test_ambiguous_record_not_counted(self):
        project = Project.objects.create(name='Без даты', status=ProjectStatus.COMPLETED)
        person = Intern.objects.create(full_name='Неоднозначный')
        TeamMember.objects.create(project=project, intern=person, role=TeamRole.BACKEND,
                                  status=TeamMember.Status.LEFT, left_reason=TeamMember.LeftReason.PROJECT_ENDED)
        self.assertNotIn(person.pk, compute().ids)
        self.assertGreaterEqual(self.client.get(reverse('interns:graduates')).context['review_count'], 1)


class ListAndDashboardTests(TestCase):
    def setUp(self):
        self.head = head()
        self.client.force_login(self.head)
        self.backend = Specialization.objects.create(name='Backend')
        self.design = Specialization.objects.create(name='UX/UI')
        self.project = create_project(Project(name='Списочный', recruitment_open=True))

    def names(self, **params):
        return [p.full_name for p in self.client.get(reverse('interns:list'), params).context['page'].object_list]

    def test_filters(self):
        osh = Intern.objects.create(full_name='Из Оша', branch='Ош', specialization=self.design, phone='+996 555 12-34-56',
                                    telegram_chat_id=42, status=InternStatus.ACTIVE)
        bish = Intern.objects.create(full_name='Из Бишкека', branch='Бишкек', specialization=self.backend,
                                     status=InternStatus.WAITING)
        member(self.project, osh, TeamRole.UXUI)
        old = graduate('Выпускник Списка', self.backend, branch='Бишкек')
        self.assertEqual(self.names(branch='Ош'), ['Из Оша'])
        self.assertEqual(self.names(q='555123456'), ['Из Оша'])
        self.assertEqual(self.names(specialization=self.design.pk), ['Из Оша'])
        self.assertEqual(self.names(telegram='yes'), ['Из Оша'])
        self.assertEqual(self.names(project=self.project.pk), ['Из Оша'])
        self.assertEqual(self.names(kind='old'), [old.full_name])
        self.assertNotIn(old.full_name, self.names(kind='new'))
        self.assertEqual(self.names(status='waiting'), [bish.full_name])

    def test_unique_people_and_pagination(self):
        person = Intern.objects.create(full_name='Аа Два Проекта', specialization=self.backend)
        graduate_person = graduate('Аа Выпускник', self.backend)
        other = create_project(Project(name='Второй', recruitment_open=True))
        member(self.project, graduate_person)
        member(other, graduate_person)
        member(self.project, person)
        for i in range(30):
            Intern.objects.create(full_name=f'Стажёр {i:02d}')
        response = self.client.get(reverse('interns:list'))
        self.assertEqual(response.context['page'].paginator.count, 32)  # люди, а не участия
        self.assertEqual(len(response.context['page'].object_list), 25)
        self.assertEqual(len(self.client.get(reverse('interns:list'), {'page': 2}).context['page'].object_list), 7)
        names = self.names(sort='name')
        self.assertEqual(names[0], 'Аа Выпускник')

    def test_warnings_and_leads_in_list(self):
        lead = Intern.objects.create(full_name='Тимлид Бэка', specialization=self.backend)
        member(self.project, lead, TeamRole.TEAM_LEAD)
        person = Intern.objects.create(full_name='С Предупреждением', specialization=self.backend)
        member(self.project, person)
        InternWarning.objects.create(intern=person, reason='Пропуск собрания')
        InternWarning.objects.create(intern=person, reason='Снятое', status=InternWarning.Status.CANCELLED)
        row = next(p for p in self.client.get(reverse('interns:list')).context['page'].object_list
                   if p.pk == person.pk)
        self.assertEqual(row.warnings_count, 1)
        self.assertEqual([l.full_name for l in row.ui_leads], ['Тимлид Бэка'])
        self.assertNotIn('Тимлид Бэка', self.names())  # тимлиды — сотрудники

    def test_dashboard_counts_match_list(self):
        new_active = Intern.objects.create(full_name='Новый Активный', status=InternStatus.ACTIVE)
        member(self.project, new_active)
        old_active = graduate('Старый Активный', self.backend)
        Intern.objects.create(full_name='Ждёт', status=InternStatus.WAITING)
        kpi = metrics.people_kpi()
        self.assertEqual(kpi['active'], len(self.names(status='active')))
        self.assertEqual(kpi['new'], len(self.names(status='active', kind='new')))
        self.assertEqual(kpi['old'], len(self.names(status='active', kind='old')))
        self.assertEqual(kpi['graduates'], self.client.get(reverse('interns:graduates')).context['total'])
        self.assertIn(old_active.full_name, self.names(kind='old'))

    def test_no_n_plus_one(self):
        def count(url):
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.client.get(url).status_code, 200)
            return len(ctx)

        for i in range(3):
            member(self.project, graduate(f'Г{i}', self.backend))
        base_list, base_grad = count(reverse('interns:list')), count(reverse('interns:graduates'))
        for i in range(12):
            p = create_project(Project(name=f'Доп {i}', recruitment_open=True))
            member(p, graduate(f'Ещё {i}', self.backend))
        self.assertLessEqual(count(reverse('interns:list')), base_list + 3)
        self.assertLessEqual(count(reverse('interns:graduates')), base_grad + 3)

    def test_long_names_and_new_ui(self):
        Intern.objects.create(full_name='Очень-Длинная-Фамилия-Без-Пробелов Константинопольская Александровна')
        for url in (reverse('interns:list'), reverse('interns:graduates'), reverse('interns:waiting')):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'gp-legacy')
        person = Intern.objects.first()
        response = self.client.get(person.get_absolute_url())
        self.assertNotContains(response, 'gp-legacy')
        self.assertContains(response, 'История')


class AccessTests(TestCase):
    def setUp(self):
        self.backend = Specialization.objects.create(name='Backend')
        self.frontend = Specialization.objects.create(name='Frontend')
        self.own = create_project(Project(name='Свой', recruitment_open=True))
        self.foreign = create_project(Project(name='Чужой', recruitment_open=True))
        self.pm, _ = portal('a-pm', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.own)
        self.lead, _ = portal('a-lead', User.Role.TEAM_LEAD, TeamRole.TEAM_LEAD, self.own, spec=self.backend)
        self.intern_user = UserModel.objects.create_user(username='a-intern', password='x', role=User.Role.INTERN)
        self.mine = Intern.objects.create(full_name='Свой Бэкендер', specialization=self.backend, phone='0700111')
        self.front = Intern.objects.create(full_name='Свой Фронт', specialization=self.frontend, phone='0700222')
        self.stranger = Intern.objects.create(full_name='Чужой Стажёр', specialization=self.backend, phone='0700333')
        member(self.own, self.mine)
        member(self.own, self.front, TeamRole.FRONTEND)
        member(self.foreign, self.stranger)

    def test_admin_pages_closed_for_staff_portals_and_interns(self):
        urls = [reverse('interns:list'), reverse('interns:graduates'), self.mine.get_absolute_url(),
                reverse('interns:internship_requests')]
        for user in (self.pm, self.lead, self.intern_user):
            self.client.force_login(user)
            for url in urls:
                with self.subTest(user=user.username, url=url):
                    response = self.client.get(url)
                    self.assertNotEqual(response.status_code, 200)
                    self.assertNotIn(b'0700333', response.content)
            response = self.client.post(reverse('interns:pause', args=[self.stranger.pk]), {'reason': 'взлом'})
            self.assertNotEqual(response.status_code, 200)
        self.stranger.refresh_from_db()
        self.assertNotEqual(self.stranger.status, InternStatus.PAUSED)

    def test_pm_sees_only_own_project_members(self):
        self.client.force_login(self.pm)
        own = self.client.get(reverse('pm_portal:intern_detail', args=[self.own.pk, self.mine.pk]))
        self.assertContains(own, '0700111')
        self.assertEqual(self.client.get(reverse('pm_portal:intern_detail', args=[self.own.pk, self.stranger.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('pm_portal:intern_detail', args=[self.foreign.pk, self.stranger.pk])).status_code, 404)

    def test_lead_sees_only_own_direction(self):
        self.client.force_login(self.lead)
        self.assertContains(self.client.get(reverse('lead_portal:intern_detail', args=[self.own.pk, self.mine.pk])), '0700111')
        self.assertEqual(self.client.get(reverse('lead_portal:intern_detail', args=[self.own.pk, self.front.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('lead_portal:intern_detail', args=[self.foreign.pk, self.stranger.pk])).status_code, 404)
        team = self.client.get(reverse('lead_portal:project_detail', args=[self.own.pk]), {'tab': 'team'})
        self.assertNotContains(team, '0700222')

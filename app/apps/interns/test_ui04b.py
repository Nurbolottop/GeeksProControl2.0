"""UI-04B: правила PM, предупреждения и решение руководителя, отчисление,
добровольная отмена, повторная стажировка через продажи и руководителя."""
import datetime
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.dashboard import metrics
from apps.dashboard.graduation import compute
from apps.interns import discipline, freeze, internships
from apps.interns.models import (
    DisciplineCase,
    Intern,
    Internship,
    InternshipRequest,
    InternStatus,
    InternWarning,
    ProfileFormLink,
)
from apps.projects import closure
from apps.projects.fixtures_closure import head, make_ready
from apps.projects.models import Project, ProjectStatus
from apps.projects.services import create_project
from apps.tasks.models import Task, TaskStatus
from apps.teams.models import TeamMember, TeamRole
from apps.teams.rules import check_assignment
from apps.training.models import Specialization

UserModel = get_user_model()
END = datetime.date(2026, 9, 30)
SALES_AGENT = '700100'


def sales_profile(intern):
    """Этап 8A: продажи сверяют направление и город (Бишкек/Ош) с профилем."""
    from apps.interns.services import branch_of
    from apps.training.models import Specialization

    if not intern.specialization_id or not intern.branch:
        spec = intern.specialization or Specialization.objects.get_or_create(name='Backend')[0]
        Intern.objects.filter(pk=intern.pk).update(specialization=spec, branch=intern.branch or 'Бишкек')
        intern.refresh_from_db()
    return intern.specialization.name, branch_of(intern)


def sales_ok(intern, reference, external_id=None):
    """Подтверждение отдела продаж — только через границу интеграции (UI-04C)."""
    from apps.interns.sales import SalesEvent, record_sales_confirmation

    direction, city = sales_profile(intern)
    with override_settings(SALES_AGENT_TELEGRAM_IDS={SALES_AGENT}):
        return record_sales_confirmation(SalesEvent(
            external_id=external_id or f'sale-{intern.pk}-{reference}', agent_telegram_id=SALES_AGENT,
            phone=intern.phone, full_name=intern.full_name, reference=reference, agent_name='Отдел продаж',
            direction=direction, city=city))


def member(project, intern, role=TeamRole.BACKEND):
    return TeamMember.objects.create(project=project, intern=intern, role=role, status=TeamMember.Status.ACTIVE)


def graduate(name, spec, role=TeamRole.BACKEND):
    done = Project.objects.create(name=f'Сданный {name}', status=ProjectStatus.COMPLETED, actual_end_date=END)
    person = Intern.objects.create(full_name=name, specialization=spec, status=InternStatus.ACTIVE)
    TeamMember.objects.create(project=done, intern=person, role=role, status=TeamMember.Status.LEFT,
                              left_reason=TeamMember.LeftReason.PROJECT_ENDED, left_at=END)
    return person


def lead_of(project, spec, name='Тимлид'):
    user = UserModel.objects.create_user(username=f'{name}-{project.pk}', password='x', role=User.Role.TEAM_LEAD)
    intern = Intern.objects.create(full_name=f'{name} {project.pk}', user=user, specialization=spec)
    member(project, intern, TeamRole.TEAM_LEAD)
    return user


class PmDirectionRulesTests(TestCase):
    def setUp(self):
        self.head = head()
        self.pm_spec = Specialization.objects.create(name='PM')
        self.p = [create_project(Project(name=f'ПМ-проект {i}', recruitment_open=True)) for i in range(3)]

    def test_same_limits_for_pm_direction(self):
        novice = Intern.objects.create(full_name='ПМ Новичок', specialization=self.pm_spec)
        member(self.p[0], novice, TeamRole.PROJECT_MANAGER)
        errors, _ = check_assignment(novice, self.p[1], TeamRole.PROJECT_MANAGER)
        self.assertTrue(any('вторая параллельная стажировка запрещена' in e for e in errors))
        old = graduate('ПМ Выпускник', self.pm_spec, TeamRole.PROJECT_MANAGER)
        member(self.p[0], old, TeamRole.PROJECT_MANAGER)
        member(self.p[1], old, TeamRole.PROJECT_MANAGER)
        self.assertTrue(check_assignment(old, self.p[2], TeamRole.PROJECT_MANAGER)[0])

    def test_head_pm_assignment_respects_rules(self):
        novice = Intern.objects.create(full_name='Назначаемый ПМ', specialization=self.pm_spec)
        self.client.force_login(self.head)
        self.client.post(reverse('teams:pm_add'), {'project': self.p[0].pk, 'intern': novice.pk})
        self.client.post(reverse('teams:pm_add'), {'project': self.p[1].pk, 'intern': novice.pk})
        self.assertEqual(TeamMember.objects.filter(intern=novice, status='active').count(), 1)

    def test_pm_direction_cannot_self_join(self):
        person = Intern.objects.create(full_name='ПМ По Ссылке', specialization=self.pm_spec, status=InternStatus.WAITING)
        from apps.interns.services import join_project_from_form

        self.assertIsNone(join_project_from_form(person, ProfileFormLink.objects.create(project=self.p[0])))
        self.assertFalse(TeamMember.objects.filter(intern=person).exists())
        pm_user = UserModel.objects.create_user(username='pm-adder', password='x', role=User.Role.PROJECT_MANAGER)
        member(self.p[0], Intern.objects.create(full_name='ПМ Проекта', user=pm_user), TeamRole.PROJECT_MANAGER)
        self.client.force_login(pm_user)
        self.client.post(reverse('pm_portal:member_add', args=[self.p[0].pk]) + '?role=pm', {'intern': person.pk, 'role': 'pm'})
        self.assertFalse(TeamMember.objects.filter(intern=person, status='active').exists())

    def test_seed_has_no_violations_and_audit_is_read_only(self):
        call_command('seed_demo', stdout=StringIO())
        from apps.teams.management.commands.membership_rule_audit import violations

        self.assertEqual(violations(), [])
        bad = Intern.objects.create(full_name='Исторический Нарушитель')
        for project in self.p[:2]:
            TeamMember.objects.create(project=project, intern=bad, role=TeamRole.BACKEND, status='active')
        out = StringIO()
        call_command('membership_rule_audit', stdout=out)
        self.assertIn('Исторический Нарушитель', out.getvalue())
        self.assertEqual(TeamMember.objects.filter(intern=bad, status='active').count(), 2)  # ничего не удалено


class WarningCycleTests(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.frontend = Specialization.objects.create(name='Frontend')
        self.project = create_project(Project(name='Дисциплина', recruitment_open=True))
        self.lead = lead_of(self.project, self.backend)
        self.user = UserModel.objects.create_user(username='w-intern', password='x', role=User.Role.INTERN)
        self.intern = Intern.objects.create(full_name='Нарушитель', specialization=self.backend,
                                            status=InternStatus.ACTIVE, user=self.user)
        member(self.project, self.intern)

    def warn(self, n, user=None):
        for i in range(n):
            discipline.issue_warning(Intern.objects.get(pk=self.intern.pk), self.project, f'Причина {i}', user or self.lead)

    def test_one_two_three_warnings(self):
        self.warn(1)
        warning = InternWarning.objects.get()
        self.assertEqual((warning.issued_by, warning.internship, warning.reason),
                         (self.lead, internships.current(self.intern), 'Причина 0'))
        self.warn(1)
        self.assertFalse(DisciplineCase.objects.exists())
        self.warn(1)
        case = DisciplineCase.objects.get()
        self.assertEqual(case.status, 'pending')
        self.assertEqual(case.warnings.count(), 3)
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.status, InternStatus.ACTIVE)  # это не отчисление
        self.warn(1)  # четвёртое при ожидающем решении — второго дела нет
        self.assertEqual(DisciplineCase.objects.count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DisciplineCase.objects.create(intern=self.intern)

    def test_who_can_warn(self):
        other_project = create_project(Project(name='Чужой', recruitment_open=True))
        foreign_lead = lead_of(other_project, self.backend, 'Чужой')
        front_lead = lead_of(self.project, self.frontend, 'Фронт')
        pm = UserModel.objects.create_user(username='w-pm', password='x', role=User.Role.PROJECT_MANAGER)
        for user in (foreign_lead, front_lead, pm):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                discipline.issue_warning(self.intern, self.project, 'x', user)
        with self.assertRaises(freeze.FreezeError):
            discipline.issue_warning(self.intern, self.project, '  ', self.lead)
        discipline.issue_warning(self.intern, self.project, 'от руководителя', self.head)
        self.assertEqual(InternWarning.objects.count(), 1)

    def test_keep_resets_and_cycle_repeats(self):
        for cycle in range(3):
            self.warn(3)
            case = DisciplineCase.objects.get(status='pending')
            discipline.decide_case(case, 'keep', self.head, f'цикл {cycle}')
            self.assertEqual(discipline.active_warnings(self.intern).count(), 0)
        self.assertEqual(InternWarning.objects.filter(status=InternWarning.Status.RESET).count(), 9)
        self.assertEqual(DisciplineCase.objects.filter(status='kept').count(), 3)
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.status, InternStatus.ACTIVE)
        with self.assertRaises(freeze.FreezeError):  # повторный POST по решённому делу
            discipline.decide_case(case, 'expel', self.head)

    def test_expel(self):
        done = Task.objects.create(project=self.project, title='Готово', status='done', assignee=self.user)
        work = Task.objects.create(project=self.project, title='В работе', status='in_progress', assignee=self.user)
        other_project = create_project(Project(name='Второй'))
        TeamMember.objects.create(project=other_project, intern=self.intern, role=TeamRole.BACKEND, status='active')
        self.warn(3)
        with self.assertRaises(PermissionDenied):
            discipline.decide_case(DisciplineCase.objects.get(), 'expel', self.lead)  # тимлид не отчисляет
        discipline.decide_case(DisciplineCase.objects.get(), 'expel', self.head, 'систематические пропуски')
        self.intern.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.intern.status, InternStatus.EXPELLED)
        self.assertFalse(self.user.is_active)
        for m in TeamMember.objects.filter(intern=self.intern):
            self.assertEqual((m.status, m.left_reason), ('left', TeamMember.LeftReason.EXPELLED))
        self.assertEqual(Task.objects.get(pk=done.pk).status, TaskStatus.DONE)
        self.assertEqual(Task.objects.get(pk=work.pk).status, TaskStatus.NEW)
        period = self.intern.internships.get()
        self.assertEqual(period.end_reason, Internship.EndReason.EXPELLED)
        self.assertIsNone(internships.current(self.intern))
        self.assertEqual(InternWarning.objects.filter(status=InternWarning.Status.CLOSED).count(), 3)
        # закрытие проекта после отчисления не выпускает
        make_ready(self.project, self.head)
        closure.close_project(self.project, self.head)
        self.assertNotIn(self.intern.pk, compute([self.intern.pk]).ids)
        # и вступить в проект без повторной стажировки нельзя
        self.assertTrue(check_assignment(self.intern, create_project(Project(name='Новый', recruitment_open=True)),
                                         TeamRole.BACKEND)[0])

    def test_lead_portal_warning_and_head_decision_views(self):
        self.client.force_login(self.lead)
        url = reverse('lead_portal:intern_warn', args=[self.project.pk, self.intern.pk])
        for i in range(3):
            self.client.post(url, {'reason': f'опоздание {i}'})
        case = DisciplineCase.objects.get()
        self.assertEqual(self.client.post(reverse('interns:discipline_decide', args=[case.pk, 'expel'])).status_code, 302)
        case.refresh_from_db()
        self.assertEqual(case.status, 'pending')  # тимлиду страница руководителя закрыта
        self.client.force_login(self.head)
        page = self.client.get(reverse('interns:internship_requests'))
        self.assertContains(page, 'опоздание 2')
        self.client.post(reverse('interns:discipline_decide', args=[case.pk, 'keep']))
        self.client.post(reverse('interns:discipline_decide', args=[case.pk, 'expel']))  # повторный POST
        case.refresh_from_db()
        self.intern.refresh_from_db()
        self.assertEqual((case.status, self.intern.status), ('kept', InternStatus.ACTIVE))


class CancelAndReenrollTests(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.project = create_project(Project(name='Отмена', recruitment_open=True))
        self.user = UserModel.objects.create_user(username='c-intern', password='x')
        self.intern = Intern.objects.create(full_name='Уходит Сам', specialization=self.backend,
                                            status=InternStatus.ACTIVE, user=self.user, phone='+996 700 11-22-33')
        member(self.project, self.intern)
        discipline.issue_warning(self.intern, self.project, 'старое предупреждение', self.head)

    def test_voluntary_cancel(self):
        work = Task.objects.create(project=self.project, title='Висит', status='review', assignee=self.user)
        done = Task.objects.create(project=self.project, title='Сделано', status='done', assignee=self.user)
        req = discipline.request_cancel(self.intern, 'Переезд')
        freeze.approve(req, self.head)
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.status, InternStatus.CANCELLED)
        self.assertEqual(TeamMember.objects.get(intern=self.intern).left_reason, TeamMember.LeftReason.CANCELLED)
        self.assertEqual(self.intern.internships.get().end_reason, Internship.EndReason.CANCELLED)
        self.assertEqual((Task.objects.get(pk=work.pk).status, Task.objects.get(pk=done.pk).status), ('new', 'done'))
        self.assertTrue(InternWarning.objects.filter(intern=self.intern).exists())  # история сохранена

    def test_dashboard_separates_exits(self):
        expelled = Intern.objects.create(full_name='Отчислен', specialization=self.backend, status=InternStatus.ACTIVE)
        member(self.project, expelled)
        with transaction.atomic():
            discipline.expel(Intern.objects.select_for_update().get(pk=expelled.pk), self.head, 'решение')
        discipline.cancel_by_head(self.intern, 'сам', self.head)
        warned = Intern.objects.create(full_name='Только предупреждён', specialization=self.backend, status=InternStatus.ACTIVE)
        member(self.project, warned)
        discipline.issue_warning(warned, self.project, 'одно', self.head)
        from django.utils import timezone

        today = timezone.localdate()
        start = today - datetime.timedelta(days=today.weekday())
        week = metrics.Week(start, start + datetime.timedelta(days=6), False)
        self.assertEqual(metrics._left_people(week, metrics.EXPELLED, set(), 'expelled'), 1)
        self.assertEqual(metrics._left_people(week, metrics.SELF_LEFT, set(), 'cancelled'), 1)
        self.assertEqual(metrics._freezes(week), 0)

    def test_reenroll_needs_sales_then_head(self):
        discipline.cancel_by_head(self.intern, 'сам', self.head)
        intern = Intern.objects.get(pk=self.intern.pk)
        # анкета и бот сами не зачисляют
        from apps.interns.services import join_project_from_form

        self.assertIsNone(join_project_from_form(intern, ProfileFormLink.objects.create(project=self.project)))
        req = discipline.request_reenroll(intern, 'повторная покупка')
        self.assertEqual(req.sales_status, InternshipRequest.Sales.PENDING)
        with self.assertRaises(freeze.FreezeError):
            freeze.approve(req, self.head)  # без продаж — нельзя
        # 11F: номер оплаты необязателен (решение руководителя) — подтверждение и без него
        self.assertEqual(sales_ok(intern, '').status, 'applied')
        self.assertEqual(InternshipRequest.objects.get(pk=req.pk).sales_reference, 'не указана')
        self.assertEqual(sales_ok(intern, 'ОПЛ-78').status, 'no_request')  # повторно — не применяется
        freeze.approve(InternshipRequest.objects.get(pk=req.pk), self.head)
        intern.refresh_from_db()
        self.assertEqual(Intern.objects.filter(full_name='Уходит Сам').count(), 1)
        self.assertEqual((intern.status, intern.internship_attempt), (InternStatus.WAITING, 2))
        # одобрение — ещё не начало: новый период откроется при вступлении в проект
        self.assertEqual([p.number for p in intern.internships.order_by('number')], [1])
        new_project = create_project(Project(name='Новая стажировка', recruitment_open=True))
        self.assertEqual(check_assignment(intern, new_project, TeamRole.BACKEND)[0], [])
        member(new_project, intern)
        periods = list(intern.internships.order_by('number'))
        self.assertEqual([p.number for p in periods], [1, 2])
        self.assertTrue(periods[1].is_open)
        self.assertEqual(discipline.active_warnings(intern).count(), 0)
        self.assertEqual(InternWarning.objects.filter(intern=intern).count(), 1)  # старое — в истории

    def test_sales_rejection_closes_request(self):
        discipline.cancel_by_head(self.intern, 'сам', self.head)
        req = discipline.request_reenroll(Intern.objects.get(pk=self.intern.pk), 'покупка')
        freeze.reject(req, self.head, 'оплаты нет')
        req.refresh_from_db()
        self.assertEqual((req.status, req.sales_status), ('rejected', 'pending'))
        self.assertEqual(Intern.objects.get(pk=self.intern.pk).status, InternStatus.CANCELLED)

    def test_reenroll_only_after_stop_and_freeze_return_unchanged(self):
        with self.assertRaises(freeze.FreezeError):
            discipline.request_reenroll(self.intern, 'покупка')  # идущая стажировка
        freeze.freeze_by_head(self.intern, 'отпуск', self.head)
        freeze.return_by_head(Intern.objects.get(pk=self.intern.pk), 'готов', self.head)
        intern = Intern.objects.get(pk=self.intern.pk)
        self.assertEqual(intern.internships.count(), 1)  # возвращение — без нового периода и оплаты
        self.assertEqual(intern.status, InternStatus.WAITING)

    def test_graduate_stays_graduate_after_reenroll(self):
        make_ready(self.project, self.head)
        closure.close_project(self.project, self.head)
        self.assertIn(self.intern.pk, compute([self.intern.pk]).ids)
        discipline.cancel_by_head(Intern.objects.get(pk=self.intern.pk), 'сам', self.head)
        req = discipline.request_reenroll(Intern.objects.get(pk=self.intern.pk), 'покупка')
        sales_ok(self.intern, 'ОПЛ-1')
        freeze.approve(InternshipRequest.objects.get(pk=req.pk), self.head)
        self.assertIn(self.intern.pk, compute([self.intern.pk]).ids)

    def test_head_ui_pages(self):
        self.client.force_login(self.head)
        card = self.client.get(self.intern.get_absolute_url())
        self.assertContains(card, 'Отмена — ушёл самостоятельно')
        self.assertNotContains(card, 'gp-legacy')
        self.client.post(reverse('interns:cancel', args=[self.intern.pk]), {'reason': 'сам'})
        self.client.post(reverse('interns:reenroll', args=[self.intern.pk]), {'reason': 'покупка'})
        req = InternshipRequest.objects.get(kind='reenroll')
        page = self.client.get(reverse('interns:internship_requests'))
        self.assertContains(page, 'Ожидает подтверждения отдела продаж')
        self.assertNotContains(page, 'Подтвердить оплату')  # руководитель не подменяет продажи
        self.client.post(reverse('interns:internship_request_decide', args=[req.pk, 'approve']))
        self.assertEqual(Intern.objects.get(pk=self.intern.pk).status, InternStatus.CANCELLED)  # без продаж — нет
        sales_ok(self.intern, 'ОПЛ-5')
        self.client.post(reverse('interns:internship_request_decide', args=[req.pk, 'approve']))
        self.assertEqual(Intern.objects.get(pk=self.intern.pk).status, InternStatus.WAITING)

"""UI-04C: выход выпускника из одного проекта, периоды стажировок,
независимое подтверждение продаж, сертификаты, сквозной сценарий этапа 5."""
import datetime
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from apps.dashboard.graduation import compute
from apps.interns import discipline, freeze, internships
from apps.interns.models import (
    Intern,
    Internship,
    InternshipCertificate,
    InternshipRequest,
    InternStatus,
    SalesConfirmation,
)
from apps.interns.sales import SalesEvent, record_sales_confirmation
from apps.projects import closure
from apps.projects.fixtures_closure import head, make_ready
from apps.projects.models import Project, ProjectStatus
from apps.projects.services import create_project
from apps.tasks.models import Task, TaskStatus
from apps.teams.models import TeamMember, TeamRole
from apps.training.models import Specialization

UserModel = get_user_model()
END = datetime.date(2026, 9, 30)
AGENT = '700200'


def member(project, intern, role=TeamRole.BACKEND, joined=None):
    return TeamMember.objects.create(project=project, intern=intern, role=role, status=TeamMember.Status.ACTIVE,
                                     joined_at=joined)


def graduate(name, spec, **extra):
    done = Project.objects.create(name=f'Сданный {name}', status=ProjectStatus.COMPLETED, actual_end_date=END)
    person = Intern.objects.create(full_name=name, specialization=spec, status=InternStatus.ACTIVE, **extra)
    TeamMember.objects.create(project=done, intern=person, role=TeamRole.BACKEND, status=TeamMember.Status.LEFT,
                              left_reason=TeamMember.LeftReason.PROJECT_ENDED, left_at=END)
    return person


def sales_profile(intern):
    """Этап 8A: продажи сверяют направление и город (Бишкек/Ош) с профилем."""
    from apps.interns.services import branch_of
    from apps.training.models import Specialization

    if not intern.specialization_id or not intern.branch:
        spec = intern.specialization or Specialization.objects.get_or_create(name='Backend')[0]
        Intern.objects.filter(pk=intern.pk).update(specialization=spec, branch=intern.branch or 'Бишкек')
        intern.refresh_from_db()
    return intern.specialization.name, branch_of(intern)


def sale(intern, reference='ОПЛ-1', external_id=None, agent=AGENT, name=None, phone=None):
    direction, city = sales_profile(intern)
    with override_settings(SALES_AGENT_TELEGRAM_IDS={AGENT}):
        return record_sales_confirmation(SalesEvent(
            external_id=external_id or f'e-{intern.pk}-{reference}', agent_telegram_id=agent,
            phone=phone if phone is not None else intern.phone, full_name=name if name is not None else intern.full_name,
            reference=reference, agent_name='Продажи', direction=direction, city=city))


class LeaveOneProjectTests(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.a = create_project(Project(name='Альфа', recruitment_open=True))
        self.b = create_project(Project(name='Бета', recruitment_open=True))
        self.user = UserModel.objects.create_user(username='l-grad', password='x')
        self.grad = graduate('Выпускник Двух', self.backend, user=self.user)
        self.ma, self.mb = member(self.a, self.grad), member(self.b, self.grad)

    def test_leave_only_selected_project(self):
        open_a = Task.objects.create(project=self.a, title='А в работе', status='in_progress', assignee=self.user)
        done_a = Task.objects.create(project=self.a, title='А готово', status='done', assignee=self.user)
        open_b = Task.objects.create(project=self.b, title='Б в работе', status='in_progress', assignee=self.user)
        with self.assertRaises(freeze.FreezeError):
            discipline.request_leave(self.grad, self.a, '  ')
        req = discipline.request_leave(self.grad, self.a, 'Нагрузка')
        self.ma.refresh_from_db()
        self.assertEqual(self.ma.status, 'active')  # до одобрения ничего не меняется
        freeze.approve(req, self.head)
        self.ma.refresh_from_db()
        self.mb.refresh_from_db()
        self.assertEqual((self.ma.status, self.ma.left_reason), ('left', TeamMember.LeftReason.LEFT_PROJECT))
        self.assertEqual(self.mb.status, 'active')
        self.assertEqual(Task.objects.get(pk=open_a.pk).status, TaskStatus.NEW)
        self.assertEqual(Task.objects.get(pk=done_a.pk).status, TaskStatus.DONE)
        self.assertEqual(Task.objects.get(pk=open_b.pk).status, 'in_progress')
        self.assertIn(self.grad.pk, compute([self.grad.pk]).ids)
        self.grad.refresh_from_db()
        self.assertEqual(self.grad.status, InternStatus.ACTIVE)
        with self.assertRaises(freeze.FreezeError):
            freeze.approve(req, self.head)  # повторное одобрение

    def test_not_for_new_intern_or_single_project(self):
        novice = Intern.objects.create(full_name='Новичок', specialization=self.backend, status=InternStatus.ACTIVE)
        member(create_project(Project(name='Один', recruitment_open=True)), novice)
        with self.assertRaises(freeze.FreezeError):
            discipline.request_leave(novice, Project.objects.get(name='Один'), 'хочу')
        TeamMember.objects.filter(pk=self.mb.pk).update(status='left')
        with self.assertRaises(freeze.FreezeError):
            discipline.request_leave(Intern.objects.get(pk=self.grad.pk), self.a, 'хочу')

    def test_recheck_at_approval(self):
        req = discipline.request_leave(self.grad, self.a, 'Нагрузка')
        make_ready(self.b, self.head)
        closure.close_project(self.b, self.head)  # второй проект закрылся до решения
        with self.assertRaises(freeze.FreezeError):
            freeze.approve(InternshipRequest.objects.get(pk=req.pk), self.head)
        self.ma.refresh_from_db()
        self.assertEqual(self.ma.status, 'active')
        self.assertEqual(InternshipRequest.objects.get(pk=req.pk).status, 'pending')

    def test_head_ui(self):
        self.client.force_login(self.head)
        card = self.client.get(self.grad.get_absolute_url())
        self.assertContains(card, 'Выход из одного проекта')
        self.client.post(reverse('interns:leave_project', args=[self.grad.pk]), {'project': self.a.pk, 'reason': 'Устал'})
        req = InternshipRequest.objects.get(kind='leave')
        self.assertContains(self.client.get(reverse('interns:internship_requests')), 'Выход из проекта')
        self.client.post(reverse('interns:internship_request_decide', args=[req.pk, 'approve']))
        self.assertEqual(TeamMember.objects.filter(intern=self.grad, status='active').count(), 1)


class PeriodTests(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.p1 = create_project(Project(name='Первый', recruitment_open=True))
        self.p2 = create_project(Project(name='Второй', recruitment_open=True))
        self.intern = Intern.objects.create(full_name='Периодов Тест', specialization=self.backend,
                                            status=InternStatus.ACTIVE, phone='0700 555 444')

    def test_full_cycle_of_periods(self):
        member(self.p1, self.intern, joined=datetime.date(2026, 9, 1))
        first = internships.current(self.intern)
        freeze.freeze_by_head(self.intern, 'отпуск', self.head)
        freeze.return_by_head(Intern.objects.get(pk=self.intern.pk), 'готов', self.head)
        self.assertEqual(self.intern.internships.count(), 1)  # заморозка — тот же период
        discipline.cancel_by_head(Intern.objects.get(pk=self.intern.pk), 'сам', self.head)
        first.refresh_from_db()
        self.assertEqual(first.end_reason, Internship.EndReason.CANCELLED)
        req = discipline.request_reenroll(Intern.objects.get(pk=self.intern.pk), 'повторная покупка')
        sale(self.intern)
        freeze.approve(InternshipRequest.objects.get(pk=req.pk), self.head)
        self.assertIsNone(internships.current(self.intern))  # одобрение — ещё не начало
        joined = datetime.date(2026, 10, 15)
        member(self.p2, Intern.objects.get(pk=self.intern.pk), joined=joined)
        second = internships.current(self.intern)
        self.assertEqual((second.number, second.started_on), (2, joined))
        self.assertEqual(self.intern.internships.count(), 2)

    def test_graduation_once_per_period(self):
        member(self.p1, self.intern)
        member(self.p2, Intern.objects.create(full_name='Другой', specialization=self.backend))
        make_ready(self.p1, self.head)
        closure.close_project(self.p1, self.head)
        period = self.intern.internships.get()
        first_date = period.graduated_on
        self.assertIsNotNone(first_date)
        member(self.p2, Intern.objects.get(pk=self.intern.pk))
        make_ready(self.p2, self.head)
        closure.close_project(self.p2, self.head)
        period.refresh_from_db()
        self.assertEqual(period.graduated_on, first_date)  # второй проект не дублирует выпуск
        self.assertFalse(InternshipCertificate.objects.exists())  # сертификат сам не создаётся

    def test_reconciliation_is_report_only(self):
        member(self.p1, self.intern)
        Internship.objects.filter(intern=self.intern).delete()
        out = StringIO()
        call_command('internship_reconciliation', stdout=out)
        self.assertIn('открытого периода нет', out.getvalue())
        self.assertFalse(Internship.objects.filter(intern=self.intern).exists())


class SalesBoundaryTests(TestCase):
    def setUp(self):
        self.head = head()
        self.intern = Intern.objects.create(full_name='Покупатель Повторный', phone='+996 (700) 12-34-56',
                                            status=InternStatus.CANCELLED)
        self.req = discipline.request_reenroll(self.intern, 'повторная покупка')

    def test_unknown_agent_and_mismatch_are_not_applied(self):
        self.assertEqual(sale(self.intern, agent='999').status, SalesConfirmation.Status.REJECTED)
        # этап 8A: телефон найден, ФИО не совпало — «данные не совпали», на ручную сверку
        self.assertEqual(sale(self.intern, reference='X', name='Совсем Другой').status, SalesConfirmation.Status.MISMATCH)
        self.assertEqual(sale(self.intern, reference='Y', phone='0555000000').status, SalesConfirmation.Status.UNMATCHED)
        self.req.refresh_from_db()
        self.assertEqual(self.req.sales_status, InternshipRequest.Sales.PENDING)

    def test_applied_event_is_recorded_and_idempotent(self):
        event = sale(self.intern, reference='ОПЛ-9', external_id='tg-1')
        self.assertEqual((event.status, event.intern, event.request), ('applied', self.intern, self.req))
        self.assertEqual(event.agent_telegram_id, AGENT)
        self.req.refresh_from_db()
        self.assertEqual((self.req.sales_status, self.req.sales_reference, self.req.sales_by),
                         (InternshipRequest.Sales.CONFIRMED, 'ОПЛ-9', None))
        self.assertEqual(sale(self.intern, reference='ОПЛ-10', external_id='tg-1').pk, event.pk)  # повтор события
        self.assertEqual(SalesConfirmation.objects.count(), 1)

    def test_head_cannot_substitute_sales(self):
        with self.assertRaises(NoReverseMatch):
            reverse('interns:sales_confirm', args=[self.req.pk])
        self.assertFalse(hasattr(discipline, 'confirm_sales'))
        with override_settings(SALES_AGENT_TELEGRAM_IDS=set()):  # по умолчанию никто не принимается
            event = record_sales_confirmation(SalesEvent(external_id='z', agent_telegram_id=AGENT,
                                                         phone=self.intern.phone, full_name=self.intern.full_name,
                                                         reference='R'))
        self.assertEqual(event.status, SalesConfirmation.Status.REJECTED)
        with self.assertRaises(freeze.FreezeError):
            freeze.approve(self.req, self.head)


class CertificateTests(TestCase):
    def setUp(self):
        self.head = head()
        self.client.force_login(self.head)
        self.intern = Intern.objects.create(full_name='С Сертификатом', status=InternStatus.ACTIVE)
        member(create_project(Project(name='Серт', recruitment_open=True)), self.intern)
        self.period = internships.current(self.intern)

    def test_no_data_then_actual_data_and_one_per_period(self):
        self.assertContains(self.client.get(self.intern.get_absolute_url()), 'Сертификат: Нет данных')
        InternshipCertificate.objects.create(internship=self.period, number='GP-2026-001',
                                             issued_on=datetime.date(2026, 10, 1))
        card = self.client.get(self.intern.get_absolute_url())
        self.assertContains(card, 'GP-2026-001')
        with self.assertRaises(IntegrityError), transaction.atomic():
            InternshipCertificate.objects.create(internship=self.period, number='GP-2026-002')


class StageFiveScenarioTests(TestCase):
    """Сквозной сценарий этапа 5: один профиль через все переходы."""

    def test_end_to_end(self):
        from apps.dashboard import metrics

        head_user = head()
        backend = Specialization.objects.create(name='Backend')
        person = Intern.objects.create(full_name='Сквозной Стажёр', specialization=backend, phone='0700 999 888',
                                       status=InternStatus.WAITING)
        p1 = create_project(Project(name='С1', recruitment_open=True))
        member(p1, person)
        make_ready(p1, head_user)
        closure.close_project(p1, head_user)                     # выпуск
        self.assertIn(person.pk, compute([person.pk]).ids)
        p2, p3 = (create_project(Project(name=n, recruitment_open=True)) for n in ('С2', 'С3'))
        member(p2, Intern.objects.get(pk=person.pk))
        member(p3, Intern.objects.get(pk=person.pk))             # выпускник в двух проектах
        freeze.approve(discipline.request_leave(Intern.objects.get(pk=person.pk), p3, 'нагрузка'), head_user)
        self.assertEqual(TeamMember.objects.filter(intern=person, status='active').count(), 1)
        for i in range(3):
            discipline.issue_warning(Intern.objects.get(pk=person.pk), p2, f'п{i}', head_user)
        discipline.decide_case(person.discipline_cases.get(), 'keep', head_user)        # «Оставить»
        for i in range(3):
            discipline.issue_warning(Intern.objects.get(pk=person.pk), p2, f'к{i}', head_user)
        discipline.decide_case(person.discipline_cases.get(status='pending'), 'expel', head_user)  # отчисление
        person.refresh_from_db()
        self.assertEqual(person.status, InternStatus.EXPELLED)
        req = discipline.request_reenroll(person, 'повторная покупка')
        sale(person, 'ОПЛ-42')
        freeze.approve(InternshipRequest.objects.get(pk=req.pk), head_user)              # повторное зачисление
        p4 = create_project(Project(name='С4', recruitment_open=True))
        member(p4, Intern.objects.get(pk=person.pk))                                      # новый период
        self.assertEqual(Intern.objects.filter(full_name='Сквозной Стажёр').count(), 1)
        self.assertEqual(list(person.internships.order_by('number').values_list('number', 'end_reason')),
                         [(1, Internship.EndReason.EXPELLED), (2, '')])
        self.assertIn(person.pk, compute([person.pk]).ids)                                # выпускник навсегда
        self.assertEqual(discipline.active_warnings(Intern.objects.get(pk=person.pk)).count(), 0)
        self.assertEqual(person.warnings.count(), 6)                                      # история цела
        kpi = metrics.people_kpi()
        self.assertEqual((kpi['old'], kpi['graduates']), (0, 1))  # статус «Ожидает» — не активный

"""Предупреждения, отчисление, добровольная отмена, повторная стажировка (UI-04B).

Предупреждения
* Выдаёт тимлид — стажёру своего направления в своём проекте (оба активны);
  руководитель — любому стажёру. Причина обязательна; предупреждение
  привязано к текущему периоду стажировки.
* Три активных предупреждения периода → дело DisciplineCase руководителю.
  Это НЕ отчисление. Одно ожидающее дело на человека (ограничение БД).
* «Оставить» — активные предупреждения становятся «Учтено» (остаются в
  журнале), счётчик снова 0; следующие три создают новое дело. Без лимита циклов.
* «Отчислить» — участия закрываются («Отчислен»), незавершённые задачи
  возвращаются «К выполнению», вход в систему закрывается, период стажировки
  закрывается, статус «Отчислен». Выпуск не присваивается.

Добровольная отмена — заявка InternshipRequest(kind=cancel), решает
руководитель: то же завершение участий, период закрывается с причиной
«Отмена — ушёл самостоятельно», статус «Отмена». Это не отчисление и не заморозка.

Повторная стажировка (после отчисления или отмены) — тот же профиль:
заявка → подтверждение продаж (apps.interns.sales — только из Telegram-модуля
продаж, руководитель его не подменяет) → одобрение руководителя
(freeze.approve) → новый период Internship, счётчик предупреждений с нуля.
Ни анкета, ни бот сами человека не зачисляют. Возвращение после заморозки —
отдельный процесс (apps.interns.freeze), оплаты и нового периода не требует.
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.interns import internships
from apps.interns.freeze import FreezeError, _notify_head, _require_head
from apps.interns.models import (
    DisciplineCase,
    Intern,
    Internship,
    InternshipRequest,
    InternStatus,
    InternWarning,
)

WARNINGS_FOR_CASE = 3
STOPPED = (InternStatus.EXPELLED, InternStatus.CANCELLED, InternStatus.DROPPED)
CANCELLABLE = (InternStatus.ACTIVE, InternStatus.READY, InternStatus.WAITING, InternStatus.PAUSED,
               InternStatus.EMPLOYABLE)


def _is_head(user):
    from apps.accounts.access import has_full_access

    return has_full_access(user)


def can_warn(user, intern, project) -> bool:
    """Руководитель — всегда; тимлид — активный в проекте и своего направления."""
    from apps.lead_portal.services import lead_own_role
    from apps.teams.models import TeamMember, TeamRole

    if _is_head(user):
        return True
    if project is None or getattr(user, 'role', '') != 'team_lead':
        return False
    if not TeamMember.objects.filter(project=project, intern__user=user, role=TeamRole.TEAM_LEAD,
                                     status=TeamMember.Status.ACTIVE).exists():
        return False
    own = lead_own_role(user)
    return bool(own) and TeamMember.objects.filter(project=project, intern=intern, role=own,
                                                   status=TeamMember.Status.ACTIVE).exists()


def active_warnings(intern, period=None):
    period = period if period is not None else internships.current(intern)
    qs = intern.warnings.filter(status=InternWarning.Status.ACTIVE)
    return qs.filter(internship=period) if period else qs.filter(internship__isnull=True)


def issue_warning(intern: Intern, project, reason: str, user) -> InternWarning:
    from apps.audit.services import log as audit_log
    from apps.teams.selectors import lead_intern_ids

    reason = (reason or '').strip()
    if not reason:
        raise FreezeError('Укажите причину предупреждения.')
    if not can_warn(user, intern, project):
        raise PermissionDenied('Предупреждение выдаёт тимлид своего направления в своём проекте или руководитель.')
    if intern.pk in lead_intern_ids():
        raise FreezeError('Тимлид — сотрудник, предупреждения стажёра к нему не применяются.')
    with transaction.atomic():
        intern = Intern.objects.select_for_update().get(pk=intern.pk)
        if intern.status in STOPPED or intern.is_archived:
            raise FreezeError('Стажировка прекращена — предупреждения не выдаются.')
        period = internships.current(intern)
        warning = InternWarning.objects.create(intern=intern, project=project, reason=reason[:2000],
                                               issued_by=user, internship=period)
        audit_log(intern, 'Предупреждение', new_value=reason[:200],
                  reason=project.name if project else '', user=user)
        count = active_warnings(intern, period).count()
        if count >= WARNINGS_FOR_CASE and not intern.discipline_cases.filter(status=DisciplineCase.Status.PENDING).exists():
            _open_case(intern, period)
    return warning


def _open_case(intern, period):
    from apps.notifications.models import NotificationLevel
    from apps.notifications.services import notify

    try:
        with transaction.atomic():
            case = DisciplineCase.objects.create(intern=intern, internship=period)
    except IntegrityError:
        return None  # дело уже ждёт решения
    active_warnings(intern, period).update(case=case)
    from django.urls import reverse

    notify(f'Три предупреждения: {intern.full_name}', level=NotificationLevel.CRITICAL,
           description='Нужно решение руководителя: отчислить или оставить.',
           url=reverse('interns:internship_requests'), dedup_key=f'discipline:{case.pk}')
    return case


def decide_case(case: DisciplineCase, decision: str, user, comment: str = '') -> DisciplineCase:
    """decision: 'keep' — оставить, 'expel' — отчислить."""
    from apps.audit.services import log as audit_log

    _require_head(user)
    if decision not in ('keep', 'expel'):
        raise FreezeError('Неизвестное решение.')
    with transaction.atomic():
        intern = Intern.objects.select_for_update().get(pk=case.intern_id)
        case = DisciplineCase.objects.select_for_update().get(pk=case.pk)
        if case.status != DisciplineCase.Status.PENDING:
            raise FreezeError('Решение по этим предупреждениям уже принято.')
        case.decided_by, case.decided_at, case.comment = user, timezone.now(), (comment or '').strip()
        if decision == 'keep':
            # счётчик — с нуля; сами предупреждения остаются в журнале
            intern.warnings.filter(status=InternWarning.Status.ACTIVE).update(status=InternWarning.Status.RESET)
            case.status = DisciplineCase.Status.KEPT
            audit_log(intern, 'Решение по трём предупреждениям: оставлен(а)', reason=case.comment, user=user)
        else:
            expel(intern, user, case.comment or 'три предупреждения')
            case.status = DisciplineCase.Status.EXPELLED
        case.save()
    return case


def _stop(intern: Intern, user, status, end_reason, left_reason, title, reason):
    """Прекратить стажировку (отчисление / отмена). Внутри транзакции."""
    from apps.audit.services import log as audit_log
    from apps.interns.freeze import end_participation

    if intern.status in STOPPED:
        raise FreezeError('Стажировка уже прекращена.')
    result = end_participation(intern, user, left_reason, title.lower())
    period = internships.current(intern)
    if period is not None:
        internships.close(period, end_reason, note=reason)
    intern.warnings.filter(status=InternWarning.Status.ACTIVE).update(status=InternWarning.Status.CLOSED)
    old = intern.get_status_display()
    intern.status = status
    intern.save(update_fields=['status', 'updated_at'])
    # Доступ в портал (ПМ-стажёр) закрываем; сотрудников (тимлидов) здесь нет
    if intern.user_id and intern.user.is_active and getattr(intern.user, 'role', '') in ('pm', 'intern'):
        intern.user.is_active = False
        intern.user.save(update_fields=['is_active'])
    audit_log(intern, title, old_value=old, new_value=intern.get_status_display(), reason=reason, user=user)
    return result


def expel(intern: Intern, user, reason: str):
    from apps.teams.models import TeamMember

    return _stop(intern, user, InternStatus.EXPELLED, Internship.EndReason.EXPELLED,
                 TeamMember.LeftReason.EXPELLED, 'Отчислен(а)', reason)


def apply_cancel(intern: Intern, user, reason: str):
    from apps.teams.models import TeamMember

    if intern.status not in CANCELLABLE:
        raise FreezeError('Отменить можно только идущую, ожидающую или замороженную стажировку.')
    return _stop(intern, user, InternStatus.CANCELLED, Internship.EndReason.CANCELLED,
                 TeamMember.LeftReason.CANCELLED, 'Отмена — ушёл(ла) самостоятельно', reason)


# --------------------------------------------------------- заявки и решения

def request_cancel(intern: Intern, reason: str, *, source=InternshipRequest.Source.BOT, user=None):
    from apps.interns.freeze import _create

    if intern.is_archived or intern.status not in CANCELLABLE:
        raise FreezeError('Отменить можно только идущую, ожидающую или замороженную стажировку.')
    req = _create(intern, InternshipRequest.Kind.CANCEL, reason, source, user)
    _notify_head(req)
    return req


def cancel_by_head(intern: Intern, reason: str, user):
    from apps.interns.freeze import approve

    _require_head(user)
    pending = intern.internship_requests.filter(kind=InternshipRequest.Kind.CANCEL,
                                                status=InternshipRequest.Status.PENDING).first()
    req = pending or request_cancel(intern, reason, source=InternshipRequest.Source.HEAD, user=user)
    return approve(req, user, comment=reason if pending else '')


def request_reenroll(intern: Intern, reason: str, *, source=InternshipRequest.Source.HEAD, user=None):
    """Повторная покупка: заявка на тот же профиль. Ждёт продаж, потом руководителя."""
    from apps.interns.freeze import _create

    if intern.status not in STOPPED:
        raise FreezeError('Повторная стажировка — только после отчисления или отмены.')
    if internships.current(intern) is not None:
        raise FreezeError('Есть незакрытый период стажировки.')
    req = _create(intern, InternshipRequest.Kind.REENROLL, reason, source, user)
    InternshipRequest.objects.filter(pk=req.pk).update(sales_status=InternshipRequest.Sales.PENDING)
    req.refresh_from_db()
    _notify_head(req)
    return req


def apply_reenroll(req: InternshipRequest, intern: Intern, user):
    """Вызывается из freeze.approve под блокировками (UI-04C).

    Одобрение — ещё не начало стажировки: человек «Ожидает стажировки», а
    новый период Internship откроется при фактическом вступлении в первый
    проект (apps.interns.internships.ensure_open, номер — следующий).
    """
    from apps.audit.services import log as audit_log

    if req.sales_status != InternshipRequest.Sales.CONFIRMED:
        raise FreezeError('Сначала нужно подтверждение продаж.')
    if intern.status not in STOPPED or internships.current(intern) is not None:
        raise FreezeError('Повторная стажировка возможна только после отчисления или отмены.')
    last = intern.internships.order_by('-number').first()
    number = (last.number if last else max(intern.internship_attempt or 1, 1)) + 1
    old = intern.get_status_display()
    intern.status = InternStatus.WAITING
    intern.internship_attempt = number
    intern.save(update_fields=['status', 'internship_attempt', 'updated_at'])
    # Доступ в портал ПМ заново выдаёт руководитель после назначения на проект
    audit_log(intern, f'Повторная стажировка №{number} одобрена', old_value=old, new_value=intern.get_status_display(),
              reason=f'{req.reason} · оплата: {req.sales_reference}', user=user)


# ------------------------------------------------ выход из одного проекта (UI-04C)

def leave_allowed(intern: Intern) -> bool:
    """Выход из одного проекта — только выпускнику с двумя активными проектами.
    Новичок и выпускник с одним проектом — заморозка или отмена."""
    from apps.dashboard.graduation import is_graduate
    from apps.teams.models import TeamMember
    from apps.teams.rules import RULE_ROLES

    active = TeamMember.objects.filter(intern=intern, status=TeamMember.Status.ACTIVE, project__isnull=False,
                                       role__in=RULE_ROLES).values('project_id').distinct().count()
    return active >= 2 and is_graduate(intern.pk)


def request_leave(intern: Intern, project, reason: str, *, source=InternshipRequest.Source.BOT, user=None):
    from apps.interns.freeze import _create
    from apps.teams.models import TeamMember

    if project is None or not TeamMember.objects.filter(intern=intern, project=project,
                                                        status=TeamMember.Status.ACTIVE).exists():
        raise FreezeError('Выберите проект, в котором вы сейчас участвуете.')
    if not leave_allowed(intern):
        raise FreezeError('Выход из одного проекта — только для выпускника в двух проектах. '
                          'Иначе — заморозка или добровольная отмена.')
    req = _create(intern, InternshipRequest.Kind.LEAVE_PROJECT, reason, source, user)
    InternshipRequest.objects.filter(pk=req.pk).update(project=project)
    req.refresh_from_db()
    _notify_head(req)
    return req


def apply_leave(req: InternshipRequest, intern: Intern, user):
    """Из freeze.approve под блокировкой стажёра: условия проверяются заново."""
    from apps.audit.services import log as audit_log
    from apps.interns.freeze import end_participation
    from apps.teams.models import TeamMember

    if intern.status in STOPPED or intern.status == InternStatus.PAUSED:
        raise FreezeError('Стажировка заморожена или прекращена — выход из проекта не нужен.')
    if not req.project_id or not TeamMember.objects.filter(intern=intern, project_id=req.project_id,
                                                           status=TeamMember.Status.ACTIVE).exists():
        raise FreezeError('Стажёр уже не участвует в этом проекте.')
    if not leave_allowed(intern):
        raise FreezeError('Сейчас активный проект один — выход из одного проекта невозможен (нужна заморозка).')
    result = end_participation(intern, user, TeamMember.LeftReason.LEFT_PROJECT,
                               'исполнитель вышел из проекта по заявке', project_ids=[req.project_id])
    audit_log(intern, 'Вышел(а) из одного проекта', new_value=req.project.name, reason=req.reason, user=user)
    return result

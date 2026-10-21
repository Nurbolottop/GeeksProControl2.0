"""Кадровый резерв и трудоустройство в Geeks Studio (этап 6B).

* Резерв — список кандидатов (прежде всего тимлидов) для Geeks Studio.
  Включение в резерв НЕ трудоустройство, НЕ завершает работу тимлида и НЕ
  отзывает проектные права. Банк резюме выпускников — отдельный модуль.
* «Трудоустройство подтверждено» ставит только confirm_employment —
  отдельное действие руководителя GeeksPro: атомарно, один раз на кандидата.
  Если у кандидата есть профиль GeeksPro — его назначения закрываются,
  вход закрывается, руководитель получает уведомления о проектах без
  тимлида. Если профиля нет — ничего лишнего не создаётся.
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.reserve.models import CandidatePool, CandidateStatus, EventKind, ReserveCandidate, StudioEmployment
from apps.reserve.services import change_status, ensure_lead_in_reserve, log_event


class EmploymentError(ValidationError):
    pass


def _require_head(user):
    from apps.accounts.access import has_full_access

    if not has_full_access(user):
        raise PermissionDenied('Кадровые решения принимает руководитель GeeksPro.')


def add_to_reserve(intern, user, comment=''):
    """Руководитель включает действующего тимлида в кадровый резерв."""
    _require_head(user)
    with transaction.atomic():
        candidate = ensure_lead_in_reserve(intern, user)
        candidate = ReserveCandidate.objects.select_for_update().get(pk=candidate.pk)
        if candidate.status == CandidateStatus.EMPLOYED:
            raise EmploymentError('Трудоустройство уже подтверждено.')
        change_status(candidate, CandidateStatus.RESERVE, comment=comment or 'Включён(а) руководителем', user=user)
    return candidate


def remove_from_reserve(candidate, user, comment=''):
    """Из активного резерва — статус «Неактивен», история остаётся."""
    _require_head(user)
    with transaction.atomic():
        candidate = ReserveCandidate.objects.select_for_update().get(pk=candidate.pk)
        if candidate.status == CandidateStatus.EMPLOYED:
            raise EmploymentError('Трудоустройство уже подтверждено — убирать из резерва нечего.')
        change_status(candidate, CandidateStatus.INACTIVE, comment=comment or 'Убран(а) из активного резерва', user=user)
    return candidate


def confirm_employment(candidate, user, employed_on=None, position='', comment=''):
    from apps.audit.services import log as audit_log
    from apps.interns import internships
    from apps.interns.freeze import end_participation
    from apps.interns.models import Intern, Internship, InternStatus, Position
    from apps.teams import staff
    from apps.teams.models import TeamMember

    _require_head(user)
    employed_on = employed_on or timezone.localdate()
    with transaction.atomic():
        candidate = ReserveCandidate.objects.select_for_update().get(pk=candidate.pk)
        if StudioEmployment.objects.filter(candidate=candidate).exists():
            raise EmploymentError('Трудоустройство уже подтверждено.')
        closed = []
        intern = Intern.objects.select_for_update().get(pk=candidate.intern_id) if candidate.intern_id else None
        if intern is not None:
            if intern.position == Position.TEAM_LEAD or intern.staff_status:
                closed = [m.project.name for m in staff.dismiss(intern, user, comment or 'Geeks Studio', studio=True)
                          if m.project_id]
                intern.refresh_from_db()
            else:
                result = end_participation(intern, user, TeamMember.LeftReason.EMPLOYED_STUDIO,
                                           'исполнитель трудоустроен в Geeks Studio')
                closed = [p.name for p in result.projects]
                period = internships.current(intern)
                if period is not None:
                    internships.close(period, Internship.EndReason.EMPLOYED, note='Geeks Studio')
                if intern.user_id and intern.user.is_active and intern.user.role in ('pm', 'intern'):
                    intern.user.is_active = False
                    intern.user.save(update_fields=['is_active'])
            intern.status = InternStatus.EMPLOYED
            intern.save(update_fields=['status', 'updated_at'])
            audit_log(intern, 'Трудоустройство в Geeks Studio подтверждено', new_value=position,
                      reason=comment, user=user)
        try:
            with transaction.atomic():
                employment = StudioEmployment.objects.create(
                    candidate=candidate, intern=intern, position=(position or '')[:255], employed_on=employed_on,
                    confirmed_by=user, comment=(comment or '').strip(), projects_closed='\n'.join(closed))
        except IntegrityError as error:
            raise EmploymentError('Трудоустройство уже подтверждено.') from error
        was = candidate.get_status_display()
        candidate.status = CandidateStatus.EMPLOYED
        candidate.status_changed_at = timezone.now()
        candidate.is_looking_for_job = False
        candidate.updated_by = user
        candidate.save(update_fields=['status', 'status_changed_at', 'is_looking_for_job', 'updated_by', 'updated_at'])
        log_event(candidate, EventKind.EMPLOYED, f'Трудоустройство в Geeks Studio подтверждено ({was} → трудоустроен)',
                  detail=' · '.join(filter(None, [f'{employed_on:%d.%m.%Y}', position, comment,
                                                  ('закрыты назначения: ' + ', '.join(closed)) if closed else ''])),
                  user=user)
    return employment


def stats() -> dict:
    from django.db.models import Count

    counts = dict(ReserveCandidate.objects.active().values_list('status').annotate(n=Count('pk')))
    return {
        'review': counts.get(CandidateStatus.REVIEW, 0) + counts.get(CandidateStatus.NEW, 0),
        'reserve': counts.get(CandidateStatus.RESERVE, 0),
        'rejected': counts.get(CandidateStatus.REJECTED, 0),
        'employed': StudioEmployment.objects.count(),
        'leads': ReserveCandidate.objects.active().filter(pool=CandidatePool.STAFF, intern__isnull=False).count(),
        'external': ReserveCandidate.objects.active().filter(intern__isnull=True).count(),
    }

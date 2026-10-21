"""Граница интеграции с отделом продаж (UI-04C).

Будущий Telegram-модуль продаж вызывает только record_sales_confirmation()
с данными события. Здесь:
* сотрудник продаж проверяется по списку SALES_AGENT_TELEGRAM_IDS
  (по умолчанию пуст — значит, ни одно событие не принимается);
* человек ищется по телефону (цифры) и сверяется ФИО;
* событие сохраняется всегда (SalesConfirmation) — с источником, автором,
  временем и результатом; повтор с тем же external_id ничего не меняет;
* только совпавшее событие при ожидающей заявке на повторную стажировку
  ставит ей «Оплата подтверждена». Решение руководителя — отдельно.

Веб-интерфейса подтверждения нет: руководитель или администратор не могут
подменить подтверждение продаж своим действием.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.interns.models import Intern, InternshipRequest, SalesConfirmation


@dataclass(frozen=True)
class SalesEvent:
    external_id: str
    agent_telegram_id: str
    phone: str
    full_name: str = ''
    reference: str = ''
    agent_name: str = ''
    occurred_at: datetime.datetime | None = None
    direction: str = ''
    city: str = ''


CITIES = ('Бишкек', 'Ош')
SALES_KINDS = (InternshipRequest.Kind.REENROLL, InternshipRequest.Kind.ENROLL)


def _digits(value: str) -> str:
    return ''.join(ch for ch in value or '' if ch.isdigit())


def _same_phone(a: str, b: str) -> bool:
    a, b = _digits(a), _digits(b)
    return bool(a and b) and (a == b or (len(a) >= 9 and len(b) >= 9 and a[-9:] == b[-9:]))


def _name_matches(intern: Intern, full_name: str) -> bool:
    if not full_name.strip():
        return False
    words = {w.lower() for w in full_name.split() if len(w) > 1}
    own = {w.lower() for w in intern.full_name.split() if len(w) > 1}
    return len(words & own) >= min(2, len(own))


def normalize_city(value: str) -> str:
    text = (value or '').strip().lower()
    for city in CITIES:
        if text == city.lower():
            return city
    return ''


def phone_candidates(phone: str) -> list[Intern]:
    digits = _digits(phone)
    if len(digits) < 9:
        return []
    from apps.interns.people import _phone_digits

    return [i for i in Intern.objects.exclude(phone='').annotate(phone_digits=_phone_digits())
            .filter(phone_digits__endswith=digits[-9:]).select_related('specialization')
            if _same_phone(i.phone, phone)]


def find_person(phone: str, full_name: str) -> Intern | None:
    """Один и тот же профиль: точное совпадение телефона и ФИО."""
    matched = [i for i in phone_candidates(phone) if _name_matches(i, full_name)]
    return matched[0] if len(matched) == 1 else None


def _profile_mismatch(intern: Intern, event: SalesEvent) -> str:
    """Направление и город из продаж должны совпасть с профилем."""
    from apps.interns.services import branch_of

    spec = intern.specialization.name if intern.specialization_id else ''
    if not event.direction.strip() or spec.casefold() != event.direction.strip().casefold():
        return f'направление: в продажах «{event.direction or "—"}», в профиле «{spec or "—"}»'
    city = normalize_city(event.city)
    if not city:
        return f'город должен быть Бишкек или Ош, указано «{event.city or "—"}»'
    if branch_of(intern) != city:
        return f'город: в продажах «{city}», в профиле «{branch_of(intern)}»'
    return ''


def record_sales_confirmation(event: SalesEvent) -> SalesConfirmation:
    """Единая точка подтверждения продаж: новая стажировка (регистрация в
    боте) и повторная. Подтверждает только при совпадении телефона, ФИО,
    направления и города (Бишкек / Ош) и при ожидающей заявке. Неоднозначное
    и несовпавшее — на ручную сверку, без зачисления."""
    from apps.audit.services import log as audit_log

    existing = SalesConfirmation.objects.filter(external_id=event.external_id).first()
    if existing is not None:
        return existing  # идемпотентно: повтор события
    occurred = event.occurred_at or timezone.now()
    base = dict(external_id=event.external_id, agent_telegram_id=str(event.agent_telegram_id),
                agent_name=event.agent_name[:255], full_name=event.full_name[:255], phone=event.phone[:32],
                reference=event.reference[:100], occurred_at=occurred, direction=event.direction[:100],
                city=event.city[:50])
    allowed = {str(x) for x in getattr(settings, 'SALES_AGENT_TELEGRAM_IDS', set())}
    try:
        with transaction.atomic():
            if str(event.agent_telegram_id) not in allowed:
                return SalesConfirmation.objects.create(status=SalesConfirmation.Status.REJECTED, **base)
            by_phone = phone_candidates(event.phone)
            matched = [i for i in by_phone if _name_matches(i, event.full_name)]
            if len(matched) > 1:
                return SalesConfirmation.objects.create(status=SalesConfirmation.Status.AMBIGUOUS,
                                                        detail=f'профилей с этим телефоном и ФИО: {len(matched)}',
                                                        **base)
            if not matched:
                status = SalesConfirmation.Status.MISMATCH if by_phone else SalesConfirmation.Status.UNMATCHED
                return SalesConfirmation.objects.create(
                    status=status, detail='ФИО не совпало с профилем этого телефона' if by_phone else '', **base)
            intern = matched[0]
            reqs = list(InternshipRequest.objects.select_for_update()
                        .filter(intern=intern, kind__in=SALES_KINDS, status=InternshipRequest.Status.PENDING,
                                sales_status=InternshipRequest.Sales.PENDING))
            if len(reqs) != 1 or not event.reference.strip():
                return SalesConfirmation.objects.create(status=SalesConfirmation.Status.NO_REQUEST, intern=intern,
                                                        **base)
            mismatch = _profile_mismatch(intern, event)
            if mismatch:
                return SalesConfirmation.objects.create(status=SalesConfirmation.Status.MISMATCH, intern=intern,
                                                        request=reqs[0], detail=mismatch[:255], **base)
            req = reqs[0]
            confirmation = SalesConfirmation.objects.create(status=SalesConfirmation.Status.APPLIED, intern=intern,
                                                            request=req, **base)
            req.sales_status = InternshipRequest.Sales.CONFIRMED
            req.sales_at, req.sales_by = occurred, None
            req.sales_reference = event.reference[:100]
            req.sales_comment = f'Отдел продаж: {event.agent_name or event.agent_telegram_id}'
            fields = ['sales_status', 'sales_at', 'sales_by', 'sales_reference', 'sales_comment', 'updated_at']
            if req.kind == InternshipRequest.Kind.ENROLL:
                # Новая стажировка: подтверждение продаж — и есть зачисление;
                # проект стажёр выбирает сам (кроме PM), как после возвращения
                req.status, req.decided_at = InternshipRequest.Status.APPROVED, occurred
                req.decision_comment = 'Оплата подтверждена отделом продаж'
                fields += ['status', 'decided_at', 'decision_comment']
            req.save(update_fields=fields)
            title = ('Регистрация подтверждена отделом продаж' if req.kind == InternshipRequest.Kind.ENROLL
                     else 'Повторная стажировка: оплата подтверждена отделом продаж')
            audit_log(intern, title, new_value=event.reference,
                      reason=f'Telegram · {event.agent_name or event.agent_telegram_id}')
            if req.kind == InternshipRequest.Kind.ENROLL:
                transaction.on_commit(lambda: _tell_enrolled(intern.pk))
            return confirmation
    except IntegrityError:
        return SalesConfirmation.objects.get(external_id=event.external_id)


def _tell_enrolled(intern_id):
    from apps.tgbot import notify

    notify.registration_confirmed(intern_id)

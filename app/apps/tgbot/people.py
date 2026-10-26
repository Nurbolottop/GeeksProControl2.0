"""Регистрация нового стажёра и привязка Telegram к существующему профилю (этап 8A).

Регистрация: один человек — один профиль. Если по телефону уже есть
профиль (действующий, замороженный, отчисленный — любой), новый не
создаётся: человек идёт в «Я уже стажёр GeeksPro» или к руководителю
(повторная стажировка — отдельный процесс). Новая регистрация — профиль
«Ожидает стажировки» и заявка «Новая стажировка» со статусом продаж
«Ждёт подтверждения»; в проект самостоятельно до подтверждения продаж не
вступить (выбор проекта открывает только record_sales_confirmation).

Привязка: телефон — только из контакта Telegram, отправленного самим
владельцем (contact.user_id == from.id); плюс ФИО. Однозначное совпадение —
привязываем Telegram, статус стажёра не трогаем. Иначе — ручная сверка
руководителем (LinkRequest), без нового профиля.
"""
from __future__ import annotations

import re

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.interns.models import Intern, InternshipRequest, InternStatus
from apps.interns.sales import CITIES, _digits, _name_matches, normalize_city, phone_candidates
from apps.tgbot.models import LinkRequest

NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁёӨөҮүҢң'\-]{2,}$")


class PeopleError(ValidationError):
    pass


def clean_name(value: str) -> str:
    from apps.tgbot import menus

    # 11F: повторное нажатие кнопки («Начать стажировку») — не ФИО
    labels = {v.casefold() for k, v in vars(menus).items() if k.isupper() and isinstance(v, str)}
    if ' '.join((value or '').split()).casefold() in labels:
        raise PeopleError('Это текст кнопки, а не имя. Напишите фамилию и имя полностью (например: Асанов Бакыт).')
    words = (value or '').split()
    if len(words) < 2 or len(' '.join(words)) > 255 or not all(NAME_RE.match(w) for w in words):
        raise PeopleError('Напишите фамилию и имя полностью, буквами (например: Асанов Бакыт).')
    return ' '.join(w[:1].upper() + w[1:] for w in words)


def clean_phone(value: str) -> str:
    digits = _digits(value)
    if not 9 <= len(digits) <= 15:
        raise PeopleError('Номер телефона: 9–15 цифр, например +996 700 123 456.')
    return ('+' + digits) if len(digits) > 9 else digits


def _lock_phone(phone: str):
    """Две одновременные регистрации с одним номером — по очереди."""
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [int(_digits(phone)[-9:]) + 8_800_000_000])


def register(*, chat_id, username, full_name, phone, specialization, city) -> Intern:
    from apps.audit.services import log as audit_log
    from apps.interns.freeze import _notify_head

    full_name, phone = clean_name(full_name), clean_phone(phone)
    city = normalize_city(city)
    if city not in CITIES:
        raise PeopleError('Город — Бишкек или Ош.')
    if specialization is None:
        raise PeopleError('Выберите направление из списка.')
    with transaction.atomic():
        _lock_phone(phone)
        if Intern.objects.filter(telegram_chat_id=chat_id).exists():
            raise PeopleError('Этот Telegram уже привязан к профилю GeeksPro.')
        if phone_candidates(phone):
            raise PeopleError('Профиль с этим номером уже есть. Нажмите «Я уже стажёр GeeksPro» — '
                              'повторная оплата и новый профиль не нужны.')
        intern = Intern.objects.create(
            full_name=full_name, phone=phone, specialization=specialization, branch=city, city=city,
            telegram_chat_id=chat_id, telegram=f'@{username}' if username else '', status=InternStatus.WAITING,
        )
        req = InternshipRequest.objects.create(
            intern=intern, kind=InternshipRequest.Kind.ENROLL, reason='Регистрация через Telegram-бот',
            source=InternshipRequest.Source.BOT, sales_status=InternshipRequest.Sales.PENDING,
        )
        audit_log(intern, 'Регистрация через Telegram-бот', new_value=f'{specialization} · {city}',
                  reason='ожидает подтверждения отдела продаж')
        transaction.on_commit(lambda: _notify_head(req))
    return intern


def link(*, chat_id, telegram_user_id, username, full_name, contact_phone, contact_user_id):
    """('linked', intern) | ('manual', LinkRequest) | ('already', intern)."""
    from apps.audit.services import log as audit_log

    if contact_user_id is None or int(contact_user_id) != int(telegram_user_id):
        raise PermissionDenied('Отправьте свой номер кнопкой «Отправить мой номер» — чужой контакт не принимается.')
    full_name = clean_name(full_name)
    phone = clean_phone(contact_phone)
    with transaction.atomic():
        current = Intern.objects.filter(telegram_chat_id=chat_id).first()
        if current is not None:
            return 'already', current
        by_phone = [i for i in phone_candidates(phone) if not i.is_archived]
        matched = [i for i in by_phone if _name_matches(i, full_name)]
        reason = ''
        if len(matched) == 1 and matched[0].telegram_chat_id in (None, chat_id):
            intern = Intern.objects.select_for_update().get(pk=matched[0].pk)
            if intern.telegram_chat_id in (None, chat_id):
                intern.telegram_chat_id = chat_id
                if username and not intern.telegram:
                    intern.telegram = f'@{username}'
                intern.save(update_fields=['telegram_chat_id', 'telegram', 'updated_at'])
                audit_log(intern, 'Telegram привязан к профилю', new_value=f'@{username}' if username else '',
                          reason='контакт Telegram + ФИО')
                return 'linked', intern
            reason = 'профиль уже привязан к другому Telegram'
        elif len(matched) == 1:
            reason = 'профиль уже привязан к другому Telegram'
        elif len(matched) > 1:
            reason = f'несколько профилей с этим телефоном и ФИО: {len(matched)}'
        elif by_phone:
            reason = 'телефон найден, ФИО не совпало'
        else:
            reason = 'профиль с этим телефоном не найден'
        try:
            with transaction.atomic():
                req = LinkRequest.objects.create(chat_id=chat_id, telegram_user_id=telegram_user_id,
                                                 telegram_username=username or '', full_name=full_name,
                                                 phone=phone, reason=reason)
                req.candidates.set(by_phone or matched or _name_candidates(full_name))
        except IntegrityError:
            req = LinkRequest.objects.get(telegram_user_id=telegram_user_id, status=LinkRequest.Status.PENDING)
            return 'manual', req
        transaction.on_commit(lambda: _notify_link(req))
    return 'manual', req


def _name_candidates(full_name):
    """Кандидаты для ручной сверки, когда по телефону никого нет (в 1.0 у части людей
    телефон не записан): непривязанные профили с тем же ФИО. Только подсказка
    руководителю — автоматически по ФИО ничего не привязывается."""
    return [i for i in Intern.objects.filter(telegram_chat_id__isnull=True, is_archived=False)
            if _name_matches(i, full_name)][:10]


def _notify_link(req):
    from django.urls import reverse

    from apps.notifications.models import NotificationLevel
    from apps.notifications.services import notify

    notify(f'Telegram: сверка профиля — {req.full_name}', level=NotificationLevel.WARNING,
           description=req.reason, url=reverse('tgbot:reconcile'), dedup_key=f'link-request:{req.pk}')


def decide_link(req: LinkRequest, user, intern: Intern | None):
    """Решение руководителя по сверке: привязать к выбранному профилю или отклонить."""
    from apps.accounts.access import has_full_access
    from apps.audit.services import log as audit_log

    if not has_full_access(user):
        raise PermissionDenied('Сверку проводит руководитель.')
    with transaction.atomic():
        req = LinkRequest.objects.select_for_update().get(pk=req.pk)
        if req.status != LinkRequest.Status.PENDING:
            raise PeopleError('Сверка уже проведена.')
        req.decided_by, req.decided_at = user, timezone.now()
        if intern is None:
            req.status = LinkRequest.Status.REJECTED
            req.save()
            from apps.tgbot import outbox

            outbox.send_after_commit('link_rejected', req.chat_id,
                                     'Профиль не подтверждён. Свяжитесь с руководителем GeeksPro.',
                                     dedup_key=f'link-rejected:{req.pk}')
            return req
        intern = Intern.objects.select_for_update().get(pk=intern.pk)
        if intern.telegram_chat_id not in (None, req.chat_id):
            raise PeopleError('Профиль уже привязан к другому Telegram.')
        if Intern.objects.filter(telegram_chat_id=req.chat_id).exclude(pk=intern.pk).exists():
            raise PeopleError('Этот Telegram уже привязан к другому профилю.')
        intern.telegram_chat_id = req.chat_id
        if req.telegram_username and not intern.telegram:
            intern.telegram = f'@{req.telegram_username}'
        intern.save(update_fields=['telegram_chat_id', 'telegram', 'updated_at'])
        req.status, req.intern = LinkRequest.Status.LINKED, intern
        req.save()
        audit_log(intern, 'Telegram привязан к профилю (сверка руководителя)', user=user,
                  new_value=req.telegram_username)
        from apps.tgbot import notify

        notify.to_intern(intern, 'link_confirmed', 'Руководитель подтвердил ваш профиль GeeksPro. Меню обновлено.',
                         dedup_key=f'link-confirmed:{req.pk}')
    return req

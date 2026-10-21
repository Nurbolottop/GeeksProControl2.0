"""Очередь и журнал исходящих сообщений Telegram (этап 8).

Каждое сообщение — запись OutboundMessage; отправка — только через
GuardedTeleBot (apps.graduate_bot.bot.bot), который сам решает, можно ли
писать этому чату (apps.common.outbound.may_send + telegram_guard). Вне
разрешённого режима запись получает статус «Задержано защитой», в сеть
ничего не уходит.

Ошибки Telegram:
* 403 (бот заблокирован / человек не начинал диалог) — «Бот заблокирован»,
  без повторов;
* 429 и 5xx, сетевые — «Повтор» с нарастающей паузой, до MAX_ATTEMPTS;
* остальное — «Не доставлено».
Текст ошибки сохраняется без URL запроса (в нём токен).
"""
from __future__ import annotations

import datetime
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.tgbot.models import OutboundMessage

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
S = OutboundMessage.Status


def _bot():
    from apps.graduate_bot.bot import bot

    return bot


def markup_to_json(markup):
    """Клавиатура telebot → dict для JSONField (у telebot есть только to_json)."""
    import json

    if markup is None or isinstance(markup, dict):
        return markup
    return json.loads(markup.to_json())


def _markup_from_json(data):
    import json

    return json.dumps(data, ensure_ascii=False) if data else None


def enqueue(kind, chat_id, text, *, intern=None, markup=None, thread_id=None, dedup_key=None) -> OutboundMessage | None:
    """Поставить в очередь. С dedup_key повтор не создаёт второе сообщение (None)."""
    status = S.QUEUED if chat_id else S.NO_CHAT
    try:
        with transaction.atomic():
            return OutboundMessage.objects.create(
                kind=kind, chat_id=chat_id or None, message_thread_id=thread_id, intern=intern, text=text[:4096],
                reply_markup=markup_to_json(markup), dedup_key=dedup_key or None, status=status)
    except IntegrityError:
        return None


def _safe_error(error) -> str:
    text = str(getattr(error, 'description', '') or error.__class__.__name__)
    return text.split('https://')[0][:255]  # URL запроса содержит токен — не сохраняем


def deliver(message: OutboundMessage) -> OutboundMessage:
    """Одна попытка отправки. Решение «можно ли» — у GuardedTeleBot."""
    from telebot.apihelper import ApiTelegramException

    from apps.common.outbound import may_send
    from apps.common.telegram_guard import TelegramBlocked

    if message.status not in (S.QUEUED, S.RETRY):
        return message
    if not message.chat_id:
        message.status = S.NO_CHAT
        message.save(update_fields=['status', 'updated_at'])
        return message
    message.attempts += 1
    kwargs = {}
    if message.reply_markup:
        kwargs['reply_markup'] = _markup_from_json(message.reply_markup)
    if message.message_thread_id:
        kwargs['message_thread_id'] = message.message_thread_id
    try:
        result = _bot().send_message(message.chat_id, message.text, **kwargs)
    except TelegramBlocked as error:
        message.status, message.last_error = S.HELD, _safe_error(error)
    except ApiTelegramException as error:
        code = getattr(error, 'error_code', 0)
        message.last_error = _safe_error(error)
        if code == 403:
            message.status = S.BOT_BLOCKED
        elif code == 429 or code >= 500:
            message.status = S.RETRY if message.attempts < MAX_ATTEMPTS else S.FAILED
        else:
            message.status = S.FAILED
            if message.message_thread_id and 'thread' in message.last_error.lower():
                _topic_missing(message)  # тему удалили — при синхронизации создастся заново
    except Exception as error:  # сеть, таймауты
        message.last_error = _safe_error(error)
        message.status = S.RETRY if message.attempts < MAX_ATTEMPTS else S.FAILED
    else:
        if result is None and not may_send(message.chat_id):
            message.status = S.HELD  # GuardedTeleBot не отправил: stage / allowlist
            message.last_error = 'исходящие запрещены режимом TELEGRAM_OUTBOUND_MODE'
        else:
            message.status, message.sent_at = S.SENT, timezone.now()
            message_id = getattr(result, 'message_id', None)
            message.telegram_message_id = message_id if isinstance(message_id, int) else None
    if message.status == S.RETRY:
        message.next_attempt_at = timezone.now() + datetime.timedelta(seconds=30 * 2 ** message.attempts)
    message.save()
    return message


def _topic_missing(message):
    from apps.tgbot.groups import mark_topic_missing
    from apps.tgbot.models import TelegramGroup

    for group in TelegramGroup.objects.filter(chat_id=message.chat_id):
        mark_topic_missing(group, message.message_thread_id)


def send(kind, chat_id, text, *, intern=None, markup=None, thread_id=None, dedup_key=None, secret_markup=False):
    """Поставить и сразу попробовать отправить (ответы в диалоге).
    secret_markup — клавиатура с одноразовой ссылкой: после попытки отправки
    в журнале не хранится (повтора нет)."""
    message = enqueue(kind, chat_id, text, intern=intern, markup=markup, thread_id=thread_id, dedup_key=dedup_key)
    if message is None:
        return None
    message = deliver(message)
    if secret_markup:
        if message.status == S.RETRY:
            message.status = S.FAILED
        message.reply_markup = {'redacted': 'одноразовая ссылка не хранится'}
        message.save(update_fields=['reply_markup', 'status', 'updated_at'])
    return message


def send_after_commit(kind, chat_id, text, **kwargs):
    """Уведомление из бизнес-операции: в очередь сейчас, отправка — после коммита."""
    message = enqueue(kind, chat_id, text, **kwargs)
    if message is not None and message.status == S.QUEUED:
        transaction.on_commit(lambda: deliver(OutboundMessage.objects.get(pk=message.pk)))
    return message


def flush(limit=100) -> int:
    """Отправить накопившееся (Celery / management-команда). Возвращает число попыток."""
    now = timezone.now()
    ids = list(OutboundMessage.objects.filter(status__in=[S.QUEUED, S.RETRY])
               .exclude(next_attempt_at__gt=now).order_by('created_at').values_list('pk', flat=True)[:limit])
    for pk in ids:
        with transaction.atomic():
            message = OutboundMessage.objects.select_for_update(skip_locked=True).filter(pk=pk).first()
            if message is not None:
                deliver(message)
    return len(ids)

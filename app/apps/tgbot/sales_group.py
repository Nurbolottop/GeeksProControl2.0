"""Сообщения группы отдела продаж (этап 8A).

Принимается только строго структурированное сообщение (единый шаблон):

    #новый_стажер
    ФИО: Асанов Бакыт Маратович
    Телефон: +996 700 123 456
    Направление: Backend
    Город: Бишкек
    Оплата: 12345

Все пять полей обязательны, каждое один раз, других строк нет. Город —
Бишкек или Ош. Сообщение принимается только:
* из чата SALES_TELEGRAM_CHAT_ID;
* от сотрудника из SALES_AGENT_TELEGRAM_IDS (проверяет record_sales_confirmation);
* не пересланное, не от бота, не отредактированное (edited_message — игнор).
Повтор того же сообщения (update) — тот же external_id: ничего не меняется.
"""
from __future__ import annotations

from django.conf import settings

TAG = '#новый_стажер'
# Метка первой строки (11F): «#новый_стажер» в любом написании; прежняя «#оплата» тоже принимается.
TAGS = {'#новыйстажер', '#оплата'}


def is_tagged(line: str) -> bool:
    return (line or '').strip().lower().replace('ё', 'е').replace('_', '').replace(' ', '') in TAGS
FIELDS = {'фио': 'full_name', 'телефон': 'phone', 'направление': 'direction', 'город': 'city', 'оплата': 'reference'}
FORWARD_KEYS = ('forward_origin', 'forward_from', 'forward_from_chat', 'forward_date', 'forward_sender_name')


class SalesFormatError(ValueError):
    pass


def is_sales_chat(chat_id) -> bool:
    chat = str(getattr(settings, 'SALES_TELEGRAM_CHAT_ID', '') or '')
    return bool(chat) and str(chat_id) == chat


def parse(text: str) -> dict:
    lines = [line.strip() for line in (text or '').strip().splitlines() if line.strip()]
    if not lines or not is_tagged(lines[0]):
        raise SalesFormatError(f'нет метки {TAG} в первой строке')
    data = {}
    for line in lines[1:]:
        key, sep, value = line.partition(':')
        field = FIELDS.get(key.strip().lower())
        if not sep or field is None:
            raise SalesFormatError(f'лишняя строка: «{line[:40]}»')
        if field in data:
            raise SalesFormatError(f'поле «{key.strip()}» указано дважды')
        if not value.strip():
            raise SalesFormatError(f'пустое поле «{key.strip()}»')
        data[field] = value.strip()
    missing = [k for k, f in FIELDS.items() if f not in data]
    if missing:
        raise SalesFormatError('нет полей: ' + ', '.join(missing))
    return data


def handle(message: dict) -> str:
    """Возвращает итог для журнала обновлений."""
    from apps.interns.sales import SalesEvent, record_sales_confirmation
    from apps.tgbot import outbox

    chat = message.get('chat') or {}
    sender = message.get('from') or {}
    text = message.get('text') or ''
    if not is_tagged((text.strip().splitlines() or [''])[0]):
        return 'sales:ignored'
    thread = message.get('message_thread_id') if message.get('is_topic_message') else None  # ответ — в ту же тему
    if any(key in message for key in FORWARD_KEYS) or sender.get('is_bot') or message.get('via_bot'):
        return 'sales:forwarded_or_bot'
    try:
        data = parse(text)
    except SalesFormatError as error:
        outbox.send('sales_reply', chat.get('id'), f'Не принято: {error}. Шаблон — в закрепе группы.', thread_id=thread)
        return 'sales:format'
    name = ' '.join(x for x in (sender.get('first_name'), sender.get('last_name')) if x)
    result = record_sales_confirmation(SalesEvent(
        external_id=f'tg:{chat.get("id")}:{message.get("message_id")}', agent_telegram_id=str(sender.get('id', '')),
        agent_name=name or sender.get('username', ''), **data))
    detail = f' ({result.detail})' if result.detail else ''
    outbox.send('sales_reply', chat.get('id'), f'{result.get_status_display()}{detail}: {data["full_name"]}',
                dedup_key=f'sales-reply:{result.external_id}', thread_id=thread)
    return f'sales:{result.status}'

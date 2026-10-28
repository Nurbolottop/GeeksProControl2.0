"""Сообщения группы отдела продаж (этап 8A).

Принимается только строго структурированное сообщение (единый шаблон):

    #новый_стажер
    ФИО: Асанов Бакыт Маратович
    Телефон: +996 700 123 456
    Направление: Backend

Обязательны ФИО, телефон и направление (11F); «Город:» и «Оплата:» можно
добавить — каждое поле один раз, других строк нет. Город, если указан, —
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
# 11F: обязательны только ФИО, телефон и направление; город и оплата — по желанию
REQUIRED = ('фио', 'телефон', 'направление')
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
    missing = [k for k in REQUIRED if FIELDS[k] not in data]
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


# ============================================ 11F: карточка регистрации с кнопкой

PAID = 'paid'


def _card_text(req) -> str:
    intern = req.intern
    return (f'🆕 Новая регистрация в боте\n\nФИО: {intern.full_name}\nТелефон: {intern.phone}\n'
            f'Направление: {intern.specialization or "—"}\nГород: {intern.city or intern.branch or "—"}\n'
            f'Telegram: {intern.telegram or "—"}')


def announce(request_id) -> bool:
    """Новая регистрация → карточка в группу продаж. Оплатил — сотрудник продаж
    жмёт «Оплата подтверждена», бот зачисляет (как по #новый_стажер)."""
    from apps.interns.models import InternshipRequest
    from apps.tgbot import menus, outbox

    chat = str(getattr(settings, 'SALES_TELEGRAM_CHAT_ID', '') or '')
    req = InternshipRequest.objects.select_related('intern__specialization').filter(pk=request_id).first()
    if not chat or req is None or req.kind != InternshipRequest.Kind.ENROLL:
        return False
    outbox.send('sales_new', chat, _card_text(req) + '\n\nОплатил(а)? Нажмите кнопку — бот зачислит и предложит проекты.',
                markup=menus.inline([[('✅ Оплата подтверждена', f'{PAID}:{req.pk}')]]),
                thread_id=getattr(settings, 'SALES_NEW_INTERNS_THREAD_ID', None), dedup_key=f'sales-new:{req.pk}')
    return True


def _answer(callback_id, text=''):
    try:
        from apps.graduate_bot.bot import bot

        bot.answer_callback_query(callback_id, text=text or None, show_alert=bool(text))
    except Exception:
        pass


def _close_card(message, text):
    try:
        from apps.graduate_bot.bot import bot

        bot.edit_message_text(text, message['chat']['id'], message['message_id'])
    except Exception:
        pass


def on_callback(payload) -> str:
    """Кнопка под карточкой регистрации. Подтверждает только сотрудник из
    SALES_AGENT_TELEGRAM_IDS — проверку делает record_sales_confirmation."""
    from apps.common import telegram_guard
    from apps.interns.models import InternshipRequest
    from apps.interns.sales import SalesEvent, record_sales_confirmation
    from apps.interns.models import SalesConfirmation

    telegram_guard.permit('callback', payload.get('id'))
    message = payload.get('message') or {}
    sender = payload.get('from') or {}
    action, _, value = (payload.get('data') or '').partition(':')
    if action != PAID or not value.isdigit():
        _answer(payload.get('id'))
        return 'sales:btn_unknown'
    allowed = {str(x) for x in getattr(settings, 'SALES_AGENT_TELEGRAM_IDS', set())}
    if str(sender.get('id', '')) not in allowed:
        _answer(payload.get('id'), 'Подтверждать оплату может только сотрудник отдела продаж.')
        return 'sales:btn_denied'
    req = InternshipRequest.objects.select_related('intern__specialization').filter(
        pk=int(value), kind=InternshipRequest.Kind.ENROLL).first()
    if req is None:
        _answer(payload.get('id'), 'Заявка не найдена.')
        return 'sales:btn_missing'
    intern = req.intern
    name = ' '.join(x for x in (sender.get('first_name'), sender.get('last_name')) if x) or sender.get('username', '')
    result = record_sales_confirmation(SalesEvent(
        external_id=f'tg-btn:{req.pk}', agent_telegram_id=str(sender.get('id', '')), agent_name=name,
        phone=intern.phone, full_name=intern.full_name, reference='кнопка в группе продаж',
        direction=intern.specialization.name if intern.specialization else '', city=intern.city or intern.branch or ''))
    if result.status == SalesConfirmation.Status.APPLIED:
        _answer(payload.get('id'))
        _close_card(message, _closed_text(req))
    else:
        detail = f' ({result.detail})' if result.detail else ''
        _answer(payload.get('id'), f'{result.get_status_display()}{detail}')
    return f'sales:btn_{result.status}'


def _closed_text(req) -> str:
    """Закрытая карточка: кто подтвердил оплату и (когда выбран) проект стажёра."""
    from apps.interns.models import SalesConfirmation

    paid = SalesConfirmation.objects.filter(external_id=f'tg-btn:{req.pk}').first()
    text = _card_text(req) + '\n\n✅ Оплата подтверждена' + (f' — {paid.agent_name}' if paid and paid.agent_name else '') + '.'
    if req.chosen_project_id:
        return text + f'\n📌 Проект: {req.chosen_project.name}'
    return text + ' Бот предложил стажёру проекты.'


def card_project_chosen(request_id) -> bool:
    """11F: стажёр выбрал проект — дописываем проект в его карточку в группе продаж."""
    from apps.interns.models import InternshipRequest
    from apps.tgbot.models import OutboundMessage

    req = InternshipRequest.objects.select_related('intern__specialization', 'chosen_project').filter(pk=request_id).first()
    if req is None or not req.chosen_project_id:
        return False
    cards = OutboundMessage.objects.filter(kind='sales_new', dedup_key__startswith=f'sales-new:{req.pk}',
                                           telegram_message_id__isnull=False).order_by('-pk')
    card = next((c for c in cards if c.dedup_key in (f'sales-new:{req.pk}', f'sales-new:{req.pk}:topic')), None)
    if card is None:
        return False
    try:
        from apps.graduate_bot.bot import bot

        bot.edit_message_text(_closed_text(req), card.chat_id, card.telegram_message_id)
    except Exception:
        return False
    return True

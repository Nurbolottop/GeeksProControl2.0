"""Диспетчер обновлений Telegram-бота 2.0 (этап 8).

handle_update(data) принимает сырой update (dict из getUpdates/webhook) и:
1. отсекает повтор по update_id (TelegramUpdate);
2. пропускает только разрешённые чаты (apps.tgbot.gate — allowlist вне prod);
3. разбирает: группа продаж, служебные/проектные группы (8B), личный чат;
4. в личном чате ведёт пошаговые сценарии; шаг хранится в БД (DialogState),
   поэтому перезапуск бота ничего не теряет.

Бизнес-логики здесь нет: регистрация — apps.tgbot.people; заявки —
apps.interns.freeze / discipline; задачи — apps.tasks.workflow; продажи —
apps.interns.sales.record_sales_confirmation. Кнопка — не право: перед
действием проверяется, что кнопка сейчас доступна этому человеку, а сервис
ещё раз проверяет полномочия. Ответы — через очередь apps.tgbot.outbox
(GuardedTeleBot), каждый — с актуальным постоянным меню.
"""
from __future__ import annotations

import logging
import re

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from apps.common import telegram_guard
from apps.tgbot import gate, menus, outbox
from apps.tgbot.models import DialogState, TelegramUpdate

logger = logging.getLogger(__name__)
URL_RE = re.compile(r'https?://\S+')
NO_NOTE = 'Без комментария'
CONFIRM, EDIT = 'Подтвердить', 'Изменить'


# ================================================================ вход

def handle_update(data: dict) -> str:
    update_id = data.get('update_id')
    if update_id is None:
        return 'invalid'
    kind = next((k for k in ('message', 'callback_query', 'edited_message', 'my_chat_member', 'chat_member')
                 if k in data), 'other')
    payload = data.get(kind) or {}
    message = payload.get('message') if kind == 'callback_query' else payload
    if kind in ('my_chat_member', 'chat_member'):
        message = payload  # у этих обновлений chat — прямо в payload
    chat = (message or {}).get('chat') or {}
    sender = payload.get('from') or {}
    try:
        with transaction.atomic():
            record = TelegramUpdate.objects.create(update_id=update_id, kind=kind, chat_id=chat.get('id'),
                                                   user_id=sender.get('id'))
    except IntegrityError:
        return 'duplicate'
    outcome = _route(kind, payload, message or {}, chat, sender)
    TelegramUpdate.objects.filter(pk=record.pk).update(outcome=outcome[:40])
    return outcome


def _route(kind, payload, message, chat, sender) -> str:
    chat_type = chat.get('type', '')
    text = (message.get('text') or '').strip()
    binding = chat_type in ('group', 'supergroup') and text.startswith('/bind')
    if kind in ('edited_message', 'other'):
        return f'ignored:{kind}'
    if not gate.inbound_allowed(chat.get('id'), chat_type, binding=binding):
        return 'skipped:not_allowed'
    try:
        if chat_type in ('group', 'supergroup'):
            return _group(kind, payload, message, chat, sender)
        if chat_type != 'private' or sender.get('is_bot'):
            return 'ignored:chat_type'
        if kind == 'callback_query':
            telegram_guard.permit('callback', payload.get('id'))
            return _callback(payload, message, sender)
        if kind == 'message':
            return _private(message, sender)
    except Exception:
        logger.exception('Ошибка обработки обновления Telegram (chat=%s)', chat.get('id'))
        _reply(chat.get('id'), 'Что-то пошло не так. Попробуйте ещё раз или нажмите «Помощь».')
        return 'error'
    return f'ignored:{kind}'


def _group(kind, payload, message, chat, sender) -> str:
    from apps.tgbot import groups, sales_group

    if kind == 'my_chat_member':
        return groups.on_bot_membership(payload)
    if kind == 'chat_member':
        return groups.on_member_update(payload)
    if kind == 'callback_query' and sales_group.is_sales_chat(chat.get('id')):
        return sales_group.on_callback(payload)  # 11F: «Оплата подтверждена» под карточкой регистрации
    if kind != 'message':
        return 'ignored:group_callback'
    if (message.get('text') or '').strip().split('@')[0].startswith('/bind'):
        return groups.on_group_message(message)  # привязка (и проверка, кто прислал) — и в группе продаж
    if sales_group.is_sales_chat(chat.get('id')):
        return sales_group.handle(message)
    return groups.on_group_message(message)


# ============================================================ помощники

def _reply(chat_id, text, markup=None, *, intern=None):
    if chat_id is None:
        return None
    if markup is None:
        markup = menus.menu(intern if intern is not None else menus.person_of(chat_id), chat_id)
    return outbox.send('reply', chat_id, text, markup=markup, intern=intern)


def _state(chat_id, user_id=None) -> DialogState:
    state, _ = DialogState.objects.get_or_create(chat_id=chat_id, defaults={'telegram_user_id': user_id})
    return state


def _set(state: DialogState, name: str, **data):
    state.state = name
    state.data = {**(state.data or {}), **data} if name else {}
    state.save(update_fields=['state', 'data', 'updated_at'])


def _clear(state: DialogState):
    _set(state, '')


def _errors(error) -> str:
    return ' '.join(getattr(error, 'messages', None) or [str(error)])


# ========================================================== личный чат

COMMANDS = {'/freeze': menus.FREEZE, '/return': menus.RETURN, '/leave': menus.LEAVE, '/projects': menus.AVAILABLE,
            '/tasks': menus.MY_TASKS, '/status': menus.MY_STATUS, '/help': menus.HELP, '/submit': menus.SUBMIT}


def _private(message, sender) -> str:
    chat_id = message['chat']['id']
    text = (message.get('text') or '').strip()
    state = _state(chat_id, sender.get('id'))
    intern = menus.person_of(chat_id)
    command = text.split()[0].split('@')[0].lower() if text.startswith('/') else ''
    if command == '/start' or text in (menus.CANCEL, '/cancel'):
        _clear(state)
        greeting = ('Здравствуйте! Это бот стажировки GeeksPro. Выберите действие кнопками внизу.'
                    if command == '/start' else 'Отменено. Возвращаю меню.')
        _reply(chat_id, greeting, intern=intern)
        return 'start' if command == '/start' else 'cancel'
    if state.state:
        step = STEPS.get(state.state)
        if step is not None:
            return step(message, sender, state, intern)
        _clear(state)  # шаг из старой версии бота — начинаем с меню
    label = COMMANDS.get(command, text)
    handler = BUTTONS.get(label)
    if handler is None:
        _reply(chat_id, 'Не понял. Выберите действие кнопками внизу.', intern=intern)
        return 'unknown'
    if label not in menus.allowed_labels(intern, chat_id):
        _reply(chat_id, 'Это действие сейчас недоступно. Меню обновлено.', intern=intern)
        return 'denied:button'
    return handler(message, sender, state, intern)


# ------------------------------------------------------------ регистрация

def start_registration(message, sender, state, intern):
    chat_id = message['chat']['id']
    if intern is not None:
        _reply(chat_id, 'Вы уже зарегистрированы в GeeksPro.', intern=intern)
        return 'reg:already'
    _set(state, 'reg_name')
    _reply(chat_id, 'Регистрация, шаг 1 из 5.\nНапишите фамилию и имя полностью.', menus.keyboard([[menus.CANCEL]]))
    return 'reg:start'


def reg_name(message, sender, state, intern):
    from apps.tgbot.people import PeopleError, clean_name

    chat_id = message['chat']['id']
    try:
        name = clean_name(message.get('text', ''))
    except PeopleError as error:
        _reply(chat_id, _errors(error), menus.keyboard([[menus.CANCEL]]))
        return 'reg:name_invalid'
    _set(state, 'reg_phone', full_name=name)
    _reply(chat_id, 'Шаг 2 из 5. Отправьте номер телефона кнопкой ниже или напишите его.', menus.contact_keyboard())
    return 'reg:name'


def _phone_from(message, sender):
    contact = message.get('contact')
    if contact:
        if contact.get('user_id') != sender.get('id'):
            raise PermissionDenied('Отправьте свой номер — чужой контакт не принимается.')
        return contact.get('phone_number', '')
    return message.get('text', '')


def reg_phone(message, sender, state, intern):
    from apps.interns.sales import phone_candidates
    from apps.training.models import Specialization
    from apps.tgbot.people import PeopleError, clean_phone

    chat_id = message['chat']['id']
    try:
        phone = clean_phone(_phone_from(message, sender))
    except (PeopleError, PermissionDenied) as error:
        _reply(chat_id, _errors(error), menus.contact_keyboard())
        return 'reg:phone_invalid'
    if phone_candidates(phone):
        _clear(state)
        _reply(chat_id, 'Профиль с этим номером уже есть в GeeksPro — новый не нужен. '
                        'Нажмите «Я уже стажёр GeeksPro».', intern=intern)
        return 'reg:duplicate'
    _set(state, 'reg_direction', phone=phone)
    names = list(Specialization.objects.order_by('name').values_list('name', flat=True))
    _reply(chat_id, 'Шаг 3 из 5. Выберите направление.', menus.choices_keyboard(names))
    return 'reg:phone'


def reg_direction(message, sender, state, intern):
    from apps.training.models import Specialization

    chat_id = message['chat']['id']
    spec = Specialization.objects.filter(name=(message.get('text') or '').strip()).first()
    if spec is None:
        names = list(Specialization.objects.order_by('name').values_list('name', flat=True))
        _reply(chat_id, 'Выберите направление кнопкой из списка.', menus.choices_keyboard(names))
        return 'reg:direction_invalid'
    _set(state, 'reg_city', specialization_id=spec.pk)
    _reply(chat_id, 'Шаг 4 из 5. Ваш город?', menus.choices_keyboard(['Бишкек', 'Ош']))
    return 'reg:direction'


def reg_city(message, sender, state, intern):
    from apps.interns.sales import normalize_city
    from apps.training.models import Specialization

    chat_id = message['chat']['id']
    city = normalize_city(message.get('text', ''))
    if not city:
        _reply(chat_id, 'Город — Бишкек или Ош.', menus.choices_keyboard(['Бишкек', 'Ош']))
        return 'reg:city_invalid'
    _set(state, 'reg_confirm', city=city)
    data = state.data
    spec = Specialization.objects.filter(pk=data.get('specialization_id')).first()
    _reply(chat_id, f'Шаг 5 из 5. Проверьте данные:\n\nФИО: {data["full_name"]}\nТелефон: {data["phone"]}\n'
                    f'Направление: {spec}\nГород: {city}',
           menus.inline([[(CONFIRM, 'reg:ok'), (EDIT, 'reg:edit')]]))
    return 'reg:city'


def reg_confirm(message, sender, state, intern):
    _reply(message['chat']['id'], 'Нажмите «Подтвердить» под сообщением с данными или «Отмена».',
           menus.keyboard([[menus.CANCEL]]))
    return 'reg:wait_confirm'


def _finish_registration(chat_id, sender, state):
    from apps.training.models import Specialization
    from apps.tgbot.people import PeopleError, register

    data = state.data or {}
    if state.state != 'reg_confirm' or not all(data.get(k) for k in ('full_name', 'phone', 'specialization_id', 'city')):
        _reply(chat_id, 'Регистрация не начата или устарела — нажмите «Начать стажировку».')
        return 'reg:stale'
    try:
        intern = register(chat_id=chat_id, username=sender.get('username', ''), full_name=data['full_name'],
                          phone=data['phone'], city=data['city'],
                          specialization=Specialization.objects.filter(pk=data['specialization_id']).first())
    except PeopleError as error:
        _clear(state)
        _reply(chat_id, _errors(error))
        return 'reg:refused'
    _clear(state)
    _reply(chat_id, 'Заявка принята!\nСтатус: ожидает подтверждения отдела продаж. '
                    'После оплаты отдел продаж подтвердит её, и бот предложит выбрать проект.', intern=intern)
    return 'reg:done'


# ----------------------------------------------------- «Я уже стажёр»

def start_link(message, sender, state, intern):
    chat_id = message['chat']['id']
    _set(state, 'link_name')
    _reply(chat_id, 'Подключим ваш профиль. Напишите ФИО так, как в анкете GeeksPro.', menus.keyboard([[menus.CANCEL]]))
    return 'link:start'


def link_name(message, sender, state, intern):
    from apps.tgbot.people import PeopleError, clean_name

    chat_id = message['chat']['id']
    try:
        name = clean_name(message.get('text', ''))
    except PeopleError as error:
        _reply(chat_id, _errors(error), menus.keyboard([[menus.CANCEL]]))
        return 'link:name_invalid'
    _set(state, 'link_contact', full_name=name)
    _reply(chat_id, 'Нажмите «Отправить мой номер» — так мы проверим, что номер ваш.', menus.contact_keyboard())
    return 'link:name'


def link_contact(message, sender, state, intern):
    from apps.tgbot.people import PeopleError, link

    chat_id = message['chat']['id']
    contact = message.get('contact')
    if not contact:
        _reply(chat_id, 'Номер принимается только кнопкой «Отправить мой номер».', menus.contact_keyboard())
        return 'link:need_contact'
    try:
        result, obj = link(chat_id=chat_id, telegram_user_id=sender.get('id'), username=sender.get('username', ''),
                           full_name=state.data.get('full_name', ''), contact_phone=contact.get('phone_number', ''),
                           contact_user_id=contact.get('user_id'))
    except (PeopleError, PermissionDenied) as error:
        _reply(chat_id, _errors(error), menus.contact_keyboard())
        return 'link:refused'
    _clear(state)
    if result == 'manual':
        _reply(chat_id, 'Не удалось однозначно найти профиль — руководитель проверит данные вручную. '
                        'Новый профиль создавать и платить повторно не нужно.')
        return 'link:manual'
    _reply(chat_id, f'Готово: профиль «{obj.full_name}» подключён. Меню обновлено.', intern=obj)
    return f'link:{result}'


# --------------------------------------------------------- информация

def show_request_status(message, sender, state, intern):
    from apps.interns.models import InternshipRequest
    from apps.tgbot.models import LinkRequest

    chat_id = message['chat']['id']
    lines = []
    if intern is not None:
        for req in intern.internship_requests.filter(status=InternshipRequest.Status.PENDING):
            extra = f' · продажи: {req.get_sales_status_display().lower()}' if req.sales_status else ''
            lines.append(f'• {req.get_kind_display()}: ожидает решения{extra}')
    link = LinkRequest.objects.filter(chat_id=chat_id).order_by('-created_at').first()
    if link and link.status == LinkRequest.Status.PENDING:
        lines.append('• Подключение профиля: ждёт сверки руководителем')
    _reply(chat_id, 'Ваши заявки:\n' + '\n'.join(lines) if lines else 'Ожидающих заявок нет.', intern=intern)
    return 'info:request_status'


def show_projects(message, sender, state, intern):
    from apps.teams.models import TeamMember

    chat_id = message['chat']['id']
    members = TeamMember.objects.filter(intern=intern, status=TeamMember.Status.ACTIVE, project__isnull=False) \
        .select_related('project').order_by('project__name')
    lines = [f'• {m.project.name} — {m.get_role_display()}' for m in members]
    _reply(chat_id, 'Ваши проекты:\n' + '\n'.join(lines) if lines else 'Активных проектов нет.', intern=intern)
    return 'info:projects'


def graduate_continue(message, sender, state, intern):
    """«Продолжить стажировку»: заявка руководителю (11F); после одобрения — выбор проекта в боте."""
    from apps.interns import freeze

    chat_id = message['chat']['id']
    try:
        req = freeze.request_continue(intern)
    except freeze.FreezeError as error:
        _reply(chat_id, _errors(error), intern=intern)
        return 'graduate:refused'
    intern.refresh_from_db()
    _reply(chat_id, ('Заявка на продолжение стажировки отправлена руководителю. Как одобрит — напишем сюда, '
                     'и вы сами выберете проект кнопкой «Доступные проекты».' if req else
                     'Ваша заявка на продолжение уже у руководителя — напишем сюда, как только он решит.'), intern=intern)
    return 'graduate:continue' if req else 'graduate:already_waiting'


def graduate_finish(message, sender, state, intern):
    from apps.graduate_bot.bot import BANK_ABOUT_TEXT

    _reply(message['chat']['id'], BANK_ABOUT_TEXT, menus.inline([[('Я согласен', 'bank:agree')]]))
    return 'graduate:bank_about'


def _bank(chat_id, intern, step):
    from apps.graduate_bot import services as graduate
    from apps.graduate_bot.bot import BANK_HOWTO_TEXT

    if menus.FINISH not in menus.allowed_labels(intern, chat_id):
        _reply(chat_id, 'Это действие сейчас недоступно.', intern=intern)
        return 'bank:denied'
    if step == 'agree':
        _reply(chat_id, BANK_HOWTO_TEXT, menus.inline([[('Отправил заявку', 'bank:sent')]]))
        return 'bank:howto'
    submitted = graduate.submit_to_resume_bank(intern, chat_id)
    intern.refresh_from_db()
    _reply(chat_id, ('Спасибо за стажировку в GeeksPro! Заявка в банк резюме ушла руководителю на проверку — '
                     'о результате напишем сюда.' if submitted else 'Ваше резюме уже принято ранее.'), intern=intern)
    return 'bank:sent' if submitted else 'bank:already'


def show_status(message, sender, state, intern):
    from apps.interns.services import branch_of

    chat_id = message['chat']['id']
    state_name = menus.state_of(intern, chat_id)
    if intern is None:
        _reply(chat_id, 'Профиль GeeksPro не подключён.')
        return 'info:status'
    lines = [f'{intern.full_name}', f'Статус: {intern.get_status_display()}',
             f'Направление: {intern.specialization or "—"}', f'Город: {branch_of(intern)}']
    if intern.staff_status:
        lines.append(f'Кадровый статус: {intern.get_staff_status_display()}')
    if state_name == menus.CHOICE:
        lines.append('Можно выбрать проект: «Доступные проекты».')
    if state_name in (menus.GRADUATE_CHOICE, menus.GRADUATE_WAITING):
        from apps.graduate_bot.services import resolved_status_message

        lines.append(resolved_status_message(intern) if state_name == menus.GRADUATE_WAITING
                     else 'Вы выпускник GeeksPro. Продолжить стажировку или закончить — выберите кнопкой.')
    _reply(chat_id, '\n'.join(lines), intern=intern)
    return 'info:status'


def show_help(message, sender, state, intern):
    chat_id = message['chat']['id']
    texts = {
        menus.GUEST: 'Новенький? «Начать стажировку». Уже стажёр GeeksPro? «Я уже стажёр GeeksPro» — подключим профиль.',
        menus.PENDING: 'Заявка ждёт подтверждения. «Статус заявки» — что с ней сейчас.',
        menus.FROZEN: 'Стажировка заморожена. Готовы продолжить — «Подать заявку на возвращение».',
        menus.CHOICE: 'Выберите проект: «Доступные проекты».',
        menus.LEAD: 'Тимлид: проекты, работы на проверке и статус отчётов. Проверка и отчёты — в Control.',
        menus.STAFF_PM: 'ПМ: проекты и статус сводного отчёта. Отчёт — в Control, раздел «Отчёты».',
        menus.STOPPED: 'Стажировка завершена. Вопросы — руководителю GeeksPro.',
        menus.GRADUATE_CHOICE: 'Вы выпускник: «Продолжить стажировку» — база ожидания проекта; '
                               '«Закончить стажировку» — банк резюме GeeksPro.',
        menus.GRADUATE_WAITING: 'Решение принято. «Мой статус» — что сейчас; новый проект назначат и сообщат сюда.',
    }
    text = texts.get(menus.state_of(intern, chat_id),
                     'Задачи: «Мои задачи» → откройте задачу → «В работу». Сдать — «Отправить работу» со скриншотом.')
    _reply(chat_id, text + '\n\n«Отмена» или /cancel — выйти из текущего шага.', intern=intern)
    return 'info:help'


# ------------------------------------------------------------ заявки

def ask_reason(kind):
    prompts = {'freeze': 'Укажите причину заморозки одним сообщением.',
               'return': 'Почему хотите вернуться? Одним сообщением.'}

    def handler(message, sender, state, intern):
        _set(state, f'{kind}_reason')
        _reply(message['chat']['id'], prompts[kind], menus.keyboard([[menus.CANCEL]]))
        return f'{kind}:ask'
    return handler


def reason_step(kind):
    def handler(message, sender, state, intern):
        chat_id = message['chat']['id']
        reason = (message.get('text') or '').strip()
        if len(reason) < 3:
            _reply(chat_id, 'Напишите причину словами.', menus.keyboard([[menus.CANCEL]]))
            return f'{kind}:reason_invalid'
        _set(state, f'{kind}_confirm', reason=reason[:2000])
        title = {'freeze': 'заморозку стажировки', 'return': 'возвращение', 'leave': 'выход из проекта'}[kind]
        _reply(chat_id, f'Отправить руководителю заявку на {title}?\nПричина: {reason[:300]}',
               menus.inline([[('Отправить заявку', f'{kind}:ok'), ('Отмена', 'cancel')]]))
        return f'{kind}:reason'
    return handler


def wait_inline(message, sender, state, intern):
    _reply(message['chat']['id'], 'Подтвердите кнопкой под сообщением или нажмите «Отмена».',
           menus.keyboard([[menus.CANCEL]]))
    return 'wait_inline'


def _submit_request(kind, chat_id, state, intern):
    from apps.interns import discipline, freeze
    from apps.projects.models import Project

    if state.state != f'{kind}_confirm' or intern is None:
        _reply(chat_id, 'Заявка устарела — начните заново из меню.', intern=intern)
        return f'{kind}:stale'
    reason = state.data.get('reason', '')
    try:
        if kind == 'freeze':
            freeze.request_freeze(intern, reason)
        elif kind == 'return':
            freeze.request_return(intern, reason)
        else:
            project = Project.objects.filter(pk=state.data.get('project_id')).first()
            discipline.request_leave(intern, project, reason)
    except ValidationError as error:
        _clear(state)
        _reply(chat_id, _errors(error), intern=intern)
        return f'{kind}:refused'
    _clear(state)
    _reply(chat_id, 'Заявка отправлена руководителю. Решение придёт сюда; «Статус заявки» — в меню.', intern=intern)
    return f'{kind}:sent'


def start_leave(message, sender, state, intern):
    from apps.teams.models import TeamMember

    chat_id = message['chat']['id']
    members = TeamMember.objects.filter(intern=intern, status=TeamMember.Status.ACTIVE, project__isnull=False) \
        .select_related('project')
    _reply(chat_id, 'Из какого проекта выйти? Второй проект останется.',
           menus.inline([[(m.project.name[:60], f'leave:{m.project_id}')] for m in members]))
    return 'leave:choose'


# ------------------------------------------------------------- проекты

def show_available(message, sender, state, intern):
    from apps.interns import freeze

    chat_id = message['chat']['id']
    if freeze.is_pm_direction(intern):
        _reply(chat_id, 'Для направления PM проект назначает руководитель.', intern=intern)
        return 'projects:pm'
    if intern.internship_requests.filter(kind='continue', status='pending').exists():
        _reply(chat_id, 'Ваша заявка на продолжение стажировки у руководителя. Как одобрит — '
                        'здесь появится выбор проекта.', intern=intern)
        return 'projects:continue_pending'
    if not freeze.can_choose(intern) and freeze.second_allowed(intern):
        return _second_list(chat_id, intern)
    if not freeze.can_choose(intern):
        from apps.projects.services import projects_open_for_recruitment

        names = [f'{p.name} — {p.recruitment_summary}' if p.recruitment_roles or p.recruitment_kind != 'all' else p.name
                 for p in projects_open_for_recruitment().order_by('name')[:15]]
        _reply(chat_id, ('Открыт набор:\n' + '\n'.join(f'• {n}' for n in names) if names else 'Сейчас набора нет.') +
               '\n\nНовые стажёры участвуют в одном проекте. Второй параллельно — для тех, кто уже '
               'заканчивал проект, и для дизайнеров, по заявке руководителю.', intern=intern)
        return 'projects:info'
    projects = freeze.available_projects(intern)
    if not projects:
        _reply(chat_id, 'Сейчас нет проектов с местом для вашего направления. Напишем, когда появятся.',
               intern=intern)
        return 'projects:none'
    lines = ['Проекты, куда можно вступить:']
    for p in projects[:20]:
        lines.append(f'\n• {p.name}' + (f' — {p.intern_brief}' if p.intern_brief else ''))
        if p.recruitment_roles or p.recruitment_kind != 'all':
            lines.append(f'  Набор: {p.recruitment_summary}')
    lines.append('\nВыберите проект (один раз):')
    _reply(chat_id, '\n'.join(lines)[:4000],
           menus.inline([[(p.name[:60], f'choose:{p.pk}')] for p in projects[:20]]))
    return 'projects:list'


def _second_list(chat_id, intern):
    """11F: стажёр уже в проекте — выбирает второй, заявка уходит руководителю."""
    from apps.interns import freeze

    pending = freeze.second_pending(intern)
    if pending:
        _reply(chat_id, f'Заявка на второй проект «{pending.chosen_project.name}» уже у руководителя. '
                        'Как решит — напишем сюда.', intern=intern)
        return 'second:pending'
    projects = freeze.second_projects(intern)
    if not projects:
        _reply(chat_id, 'Сейчас нет проектов с открытым набором для вашего направления. Напишем, когда появятся.',
               intern=intern)
        return 'second:none'
    lines = ['Можно участвовать параллельно ещё в одном проекте. Выберите — заявка уйдёт руководителю:']
    for p in projects[:20]:
        lines.append(f'\n• {p.name}' + (f' — {p.intern_brief}' if p.intern_brief else ''))
    _reply(chat_id, '\n'.join(lines)[:4000], menus.inline([[(p.name[:60], f'second:{p.pk}')] for p in projects[:20]]))
    return 'second:list'


def _second(chat_id, intern, project_id, *, confirm):
    from apps.interns import freeze
    from apps.projects.models import Project

    project = Project.objects.filter(pk=project_id).first()
    if project is None or project not in freeze.second_projects(intern):
        _reply(chat_id, 'В этот проект сейчас нельзя подать заявку.', intern=intern)
        return 'second:unavailable'
    if not confirm:
        about = f'\n{project.intern_brief}\n' if project.intern_brief else ''
        _reply(chat_id, f'Отправить руководителю заявку на участие в проекте «{project.name}»?{about}\n'
                        'Текущий проект остаётся за вами.',
               menus.inline([[('Да, отправить', f'second_ok:{project.pk}'), ('Отмена', 'cancel')]]))
        return 'second:confirm'
    try:
        freeze.request_second(intern, project)
    except (ValidationError, freeze.FreezeError) as error:
        _reply(chat_id, _errors(error), intern=intern)
        return 'second:refused'
    _reply(chat_id, f'Заявка на участие в «{project.name}» отправлена руководителю. Как решит — напишем сюда.',
           intern=intern)
    return 'second:sent'


# -------------------------------------------------------------- задачи

def _assignment(intern, task_id):
    from apps.tasks import workflow

    return workflow.intern_tasks(intern).filter(task_id=task_id).first() if intern else None


def show_tasks(message, sender, state, intern, *, for_submit=False):
    from apps.tasks import workflow
    from apps.tasks.models import TaskStatus

    chat_id = message['chat']['id']
    items = list(workflow.intern_tasks(intern)[:20])
    if for_submit:
        items = [a for a in items if a.status in (TaskStatus.NEW, TaskStatus.IN_PROGRESS)]
    if not items:
        _reply(chat_id, 'Задач для сдачи нет.' if for_submit else 'Назначенных задач нет.', intern=intern)
        return 'tasks:empty'
    prefix = 'submit' if for_submit else 'task'
    rows = [[(f'{a.task.title[:40]} · {a.get_status_display()}', f'{prefix}:{a.task_id}')] for a in items]
    _reply(chat_id, 'Какую работу сдать?' if for_submit else 'Ваши задачи:', menus.inline(rows))
    return 'tasks:list'


def _task_card(chat_id, intern, task_id):
    from apps.tasks.models import TaskStatus

    assignment = _assignment(intern, task_id)
    if assignment is None:
        _reply(chat_id, 'Эта задача вам не назначена.', intern=intern)
        return 'tasks:denied'
    task = assignment.task
    last = assignment.submissions.order_by('-created_at').first()
    lines = [f'«{task.title}»', f'Проект: {task.project.name}', f'Ваш результат: {assignment.get_status_display()}',
             f'Задача: {task.get_status_display()}']
    if task.deadline:
        lines.append(f'Срок: {task.deadline:%d.%m.%Y}')
    if task.description:
        lines.append(f'\n{task.description[:1500]}')
    if last:
        lines.append(f'\nПоследняя сдача (попытка {last.attempt}): {last.get_outcome_display().lower()}')
        if last.review_comment:
            lines.append(f'Комментарий проверки: {last.review_comment[:500]}')
    buttons = []
    if assignment.status == TaskStatus.NEW:
        buttons.append(('В работу', f'start:{task.pk}'))
    if assignment.status in (TaskStatus.NEW, TaskStatus.IN_PROGRESS):
        buttons.append(('Отправить работу', f'submit:{task.pk}'))
    _reply(chat_id, '\n'.join(lines), menus.inline([buttons]) if buttons else None, intern=intern)
    return 'tasks:card'


def _ask_screenshot(chat_id, state, intern, task_id):
    from apps.tasks.models import TaskStatus

    assignment = _assignment(intern, task_id)
    if assignment is None or assignment.status not in (TaskStatus.NEW, TaskStatus.IN_PROGRESS):
        _reply(chat_id, 'Эту задачу сейчас сдать нельзя.', intern=intern)
        return 'submit:denied'
    _set(state, 'submit_photo', task_id=task_id)
    _reply(chat_id, f'«{assignment.task.title}»\nПришлите скриншот результата (фото или изображение файлом).',
           menus.keyboard([[menus.CANCEL]]))
    return 'submit:ask_photo'


def submit_photo(message, sender, state, intern):
    from apps.tgbot.files import screenshot_from

    chat_id = message['chat']['id']
    found = screenshot_from(message)
    if not found or not found[0]:
        _reply(chat_id, 'Нужен скриншот: фото или изображение файлом.', menus.keyboard([[menus.CANCEL]]))
        return 'submit:need_photo'
    _set(state, 'submit_note', file_id=found[0], file_size=found[1])
    _reply(chat_id, 'Добавьте ссылку и/или комментарий одним сообщением — или «Без комментария».',
           menus.keyboard([[NO_NOTE], [menus.CANCEL]]))
    return 'submit:photo'


def submit_note(message, sender, state, intern):
    from apps.tasks import workflow
    from apps.tgbot.files import FileRejected, download

    chat_id = message['chat']['id']
    text = (message.get('text') or '').strip()
    if not text:
        _reply(chat_id, 'Напишите ссылку/комментарий или нажмите «Без комментария».',
               menus.keyboard([[NO_NOTE], [menus.CANCEL]]))
        return 'submit:need_note'
    links = ' '.join(URL_RE.findall(text)) if text != NO_NOTE else ''
    comment = URL_RE.sub('', text).strip() if text != NO_NOTE else ''
    data = state.data
    # file_id получен в принятом сообщении этого же чата (хранится в его шаге)
    telegram_guard.permit('file', data.get('file_id'))
    try:
        screenshot = download(data.get('file_id'), data.get('file_size') or 0,
                              prefix=f'task{data.get("task_id")}')
        submission = workflow.submit_from_telegram(chat_id, data.get('task_id'), screenshot=screenshot,
                                                   links=links, comment=comment)
    except (FileRejected, ValidationError, telegram_guard.TelegramBlocked) as error:
        _clear(state)
        _reply(chat_id, f'Не отправлено: {_errors(error)}', intern=intern)
        return 'submit:refused'
    _clear(state)
    _reply(chat_id, f'Работа отправлена на проверку (попытка {submission.attempt}). '
                    'Результат проверки придёт сюда.', intern=intern)
    return 'submit:done'


# ------------------------------------------------------ тимлид и ПМ

def show_reviews(message, sender, state, intern):
    from apps.tasks.models import TaskSubmission
    from apps.teams.models import TeamMember, TeamRole

    chat_id = message['chat']['id']
    projects = TeamMember.objects.filter(intern=intern, role=TeamRole.TEAM_LEAD, status=TeamMember.Status.ACTIVE) \
        .values_list('project_id', flat=True)
    pending = TaskSubmission.objects.filter(outcome=TaskSubmission.Outcome.PENDING, task__project_id__in=projects) \
        .select_related('task__project', 'assignment')[:20]
    lines = [f'• «{s.task.title}» — {s.assignment.person_name} ({s.task.project.name})' for s in pending]
    _reply(chat_id, ('На проверке:\n' + '\n'.join(lines) + '\n\nПроверка — в Control: проект → «Задачи».')
           if lines else 'Работ на проверке нет.', intern=intern)
    return 'lead:reviews'


def show_lead_reports(message, sender, state, intern):
    from apps.dashboard.metrics import week_of
    from apps.reports.weekly import lead_obligations

    chat_id = message['chat']['id']
    from django.utils import timezone

    week = week_of(timezone.localdate())
    lines = [f'• {o.project.name}: {o.label.lower()}' for o in lead_obligations(week, lead=intern)]
    _reply(chat_id, (f'Отчёты за неделю {week.label}:\n' + '\n'.join(lines) +
                     '\n\nСдать — в Control: «Отчёты и оценки».') if lines else 'Обязательных отчётов нет.',
           intern=intern)
    return 'lead:reports'


def show_pm_report(message, sender, state, intern):
    from django.utils import timezone

    from apps.dashboard.metrics import week_of
    from apps.reports.models import PmWeeklyReport

    chat_id = message['chat']['id']
    week = week_of(timezone.localdate())
    report = PmWeeklyReport.objects.filter(pm=intern, week_start=week.start).first()
    text = (f'Сводный отчёт за {week.label} сдан {report.created_at:%d.%m %H:%M}'
            f'{" (в срок)" if report.on_time else " (не в срок)"}.') if report else \
        f'Сводный отчёт за {week.label} ещё не сдан. Срок — пятница или суббота; сдать — в Control: «Отчёты».'
    _reply(chat_id, text, intern=intern)
    return 'pm:report'


# ======================================================== callback-и

def _callback(payload, message, sender) -> str:
    from apps.common.outbound import may_send

    chat_id = (message.get('chat') or {}).get('id')
    data = payload.get('data') or ''
    if may_send(chat_id):
        try:
            from apps.graduate_bot.bot import bot

            bot.answer_callback_query(payload.get('id'))
        except Exception:
            logger.info('answerCallbackQuery не выполнен (chat=%s)', chat_id)
    state = _state(chat_id, sender.get('id'))
    intern = menus.person_of(chat_id)
    action, _, value = data.partition(':')
    labels = menus.allowed_labels(intern, chat_id)
    if action == 'cancel':
        _clear(state)
        _reply(chat_id, 'Отменено.', intern=intern)
        return 'cb:cancel'
    if action == 'reg':
        if value == 'edit':
            return start_registration(message, sender, state, None) if intern is None else 'cb:reg_denied'
        return _finish_registration(chat_id, sender, state)
    if action in ('freeze', 'return', 'leave') and value == 'ok':
        return _submit_request(action, chat_id, state, intern)
    if action == 'leave' and value.isdigit():
        if menus.LEAVE not in labels:
            _reply(chat_id, 'Выход из проекта сейчас недоступен.', intern=intern)
            return 'cb:denied'
        _set(state, 'leave_reason', project_id=int(value))
        _reply(chat_id, 'Укажите причину выхода из проекта.', menus.keyboard([[menus.CANCEL]]))
        return 'leave:ask'
    if action == 'meetjoin' and value.isdigit():
        return _meeting_entry(chat_id, intern, int(value))
    if action == 'bank' and value in ('agree', 'sent'):
        return _bank(chat_id, intern, value)
    if action in ('second', 'second_ok') and value.isdigit():
        if menus.AVAILABLE not in labels:
            _reply(chat_id, 'Выбор проекта сейчас недоступен.', intern=intern)
            return 'cb:denied'
        return _second(chat_id, intern, int(value), confirm=action == 'second_ok')
    if action in ('choose', 'choose_ok') and value.isdigit():
        return _choose(chat_id, intern, int(value), confirm=action == 'choose_ok')
    if action in ('task', 'start', 'submit') and value.isdigit():
        if menus.MY_TASKS not in labels:
            _reply(chat_id, 'Задачи сейчас недоступны.', intern=intern)
            return 'cb:denied'
        if action == 'task':
            return _task_card(chat_id, intern, int(value))
        if action == 'submit':
            return _ask_screenshot(chat_id, state, intern, int(value))
        return _start_task(chat_id, intern, int(value))
    _reply(chat_id, 'Кнопка устарела — выберите действие в меню.', intern=intern)
    return 'cb:unknown'


def _meeting_entry(chat_id, intern, participant_pk):
    """«Войти на собрание»: личность — подтверждённый Telegram участника
    (нажатие пришло из его чата). Ответ — одноразовая ссылка на 5 минут."""
    from apps.video import services as video

    try:
        code = video.issue_entry(participant_pk, chat_id)
    except (PermissionDenied, ValidationError) as error:
        _reply(chat_id, _errors(error), intern=intern)
        return 'meeting:denied'
    outbox.send('meeting_entry', chat_id, 'Ссылка на вход — одноразовая, действует 5 минут. Не пересылайте её.',
                markup=menus.url_button('Открыть видеовстречу', video.entry_link(code)), intern=intern,
                secret_markup=True)
    return 'meeting:entry'


def _start_task(chat_id, intern, task_id):
    from apps.tasks import workflow

    if _assignment(intern, task_id) is None:
        _reply(chat_id, 'Эта задача вам не назначена.', intern=intern)
        return 'tasks:denied'
    try:
        _assignment_obj, changed = workflow.start_work(task_id, intern)
    except ValidationError as error:
        _reply(chat_id, _errors(error), intern=intern)
        return 'tasks:refused'
    _reply(chat_id, 'Задача в работе.' if changed else 'Задача уже в работе.', intern=intern)
    return 'tasks:started'


def _choose(chat_id, intern, project_id, *, confirm):
    from apps.interns import freeze
    from apps.projects.models import Project

    if menus.AVAILABLE not in menus.allowed_labels(intern, chat_id) or not freeze.can_choose(intern):
        _reply(chat_id, 'Выбор проекта сейчас недоступен.', intern=intern)
        return 'choose:denied'
    project = Project.objects.filter(pk=project_id).first()
    if project is None or project not in freeze.available_projects(intern):
        _reply(chat_id, 'В этом проекте нет места для вашего направления.', intern=intern)
        return 'choose:unavailable'
    if not confirm:
        about = f'\n{project.intern_brief}\n' if project.intern_brief else ''
        _reply(chat_id, f'Вступить в проект «{project.name}»?{about}\nВыбор делается один раз.',
               menus.inline([[('Да, вступить', f'choose_ok:{project.pk}'), ('Отмена', 'cancel')]]))
        return 'choose:confirm'
    try:
        freeze.choose_project(intern, project)
    except ValidationError as error:
        _reply(chat_id, _errors(error), intern=intern)
        return 'choose:refused'
    intern.refresh_from_db()
    _reply(chat_id, 'Готово — проект выбран. Подробности — в следующем сообщении.', intern=intern)
    return 'choose:done'


# =========================================================== таблицы

BUTTONS = {
    menus.START: start_registration,
    menus.ALREADY: start_link,
    menus.HELP: show_help,
    menus.REQUEST_STATUS: show_request_status,
    menus.MY_PROJECTS: show_projects,
    menus.MY_TASKS: show_tasks,
    menus.SUBMIT: lambda m, s, st, i: show_tasks(m, s, st, i, for_submit=True),
    menus.MY_STATUS: show_status,
    menus.FREEZE: ask_reason('freeze'),
    menus.RETURN: ask_reason('return'),
    menus.AVAILABLE: show_available,
    menus.LEAVE: start_leave,
    menus.REVIEWS: show_reviews,
    menus.LEAD_REPORTS: show_lead_reports,
    menus.PM_REPORT: show_pm_report,
    menus.CONTINUE: graduate_continue,
    menus.FINISH: graduate_finish,
}

STEPS = {
    'reg_name': reg_name, 'reg_phone': reg_phone, 'reg_direction': reg_direction, 'reg_city': reg_city,
    'reg_confirm': reg_confirm,
    'link_name': link_name, 'link_contact': link_contact,
    'freeze_reason': reason_step('freeze'), 'return_reason': reason_step('return'),
    'leave_reason': reason_step('leave'),
    'freeze_confirm': wait_inline, 'return_confirm': wait_inline, 'leave_confirm': wait_inline,
    'submit_photo': submit_photo, 'submit_note': submit_note,
}

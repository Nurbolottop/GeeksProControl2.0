"""Постоянное нижнее меню бота (этап 8A).

ReplyKeyboardMarkup(is_persistent=True, resize_keyboard=True,
one_time_keyboard=False) — прикладывается к каждому ответу бота, поэтому
клавиатура не исчезает и обновляется после смены статуса. Набор кнопок
вычисляется из реального состояния профиля (статус, заявки, назначения).
Кнопка — не право: каждый обработчик заново проверяет полномочия на сервере.
"""
from __future__ import annotations

from telebot import types

# --- подписи кнопок (они же — тексты, которые присылает Telegram)
START = 'Начать стажировку'
ALREADY = 'Я уже стажёр GeeksPro'
HELP = 'Помощь'
REQUEST_STATUS = 'Статус заявки'
MY_PROJECTS = 'Мои проекты'
MY_TASKS = 'Мои задачи'
SUBMIT = 'Отправить работу'
MY_STATUS = 'Мой статус'
FREEZE = 'Заморозить стажировку'
RETURN = 'Подать заявку на возвращение'
AVAILABLE = 'Доступные проекты'
LEAVE = 'Выйти из проекта'
REVIEWS = 'Работы на проверке'
LEAD_REPORTS = 'Мои отчёты'
PM_REPORT = 'Отчёт ПМ'
CANCEL = 'Отмена'
CONTINUE = 'Продолжить стажировку'
FINISH = 'Закончить стажировку'

GUEST, PENDING, ACTIVE, FROZEN, CHOICE, STOPPED, LEAD, STAFF_PM = (
    'guest', 'pending', 'active', 'frozen', 'choice', 'stopped', 'lead', 'pm')
GRADUATE_CHOICE, GRADUATE_WAITING = 'graduate_choice', 'graduate_waiting'  # перенесено из бота-выпускника (10A)


def keyboard(rows) -> types.ReplyKeyboardMarkup:
    markup = types.ReplyKeyboardMarkup(is_persistent=True, resize_keyboard=True, one_time_keyboard=False)
    for row in rows:
        markup.row(*[types.KeyboardButton(label) for label in row])
    return markup


def contact_keyboard() -> types.ReplyKeyboardMarkup:
    markup = types.ReplyKeyboardMarkup(is_persistent=True, resize_keyboard=True, one_time_keyboard=False)
    markup.row(types.KeyboardButton('📱 Отправить мой номер', request_contact=True))
    markup.row(types.KeyboardButton(CANCEL))
    return markup


def choices_keyboard(labels, columns=2) -> types.ReplyKeyboardMarkup:
    rows = [labels[i:i + columns] for i in range(0, len(labels), columns)]
    return keyboard(rows + [[CANCEL]])


def url_button(text, url) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton(text, url=url))
    return markup


def inline(rows) -> types.InlineKeyboardMarkup:
    """rows: [[(подпись, callback_data), …], …] — для выбора и подтверждений."""
    markup = types.InlineKeyboardMarkup()
    for row in rows:
        markup.row(*[types.InlineKeyboardButton(text, callback_data=data) for text, data in row])
    return markup


# ------------------------------------------------------------ состояние

def person_of(chat_id):
    from apps.interns.models import Intern

    return Intern.objects.filter(telegram_chat_id=chat_id, is_archived=False).select_related(
        'specialization', 'user').first()


def _pending_link(chat_id) -> bool:
    from apps.tgbot.models import LinkRequest

    return LinkRequest.objects.filter(chat_id=chat_id, status=LinkRequest.Status.PENDING).exists()


def is_staff_lead(intern) -> bool:
    from apps.teams.staff import is_staff_lead as check

    return check(intern) and intern.staff_status == 'active'


def is_active_pm(intern) -> bool:
    from apps.teams.models import TeamMember, TeamRole

    return TeamMember.objects.filter(intern=intern, role=TeamRole.PROJECT_MANAGER,
                                     status=TeamMember.Status.ACTIVE).exists()


def state_of(intern, chat_id=None) -> str:
    from apps.interns import freeze
    from apps.interns.models import InternshipRequest, InternStatus

    if intern is None:
        return PENDING if chat_id and _pending_link(chat_id) else GUEST
    if intern.staff_status in ('dismissed', 'studio'):
        return STOPPED
    if is_staff_lead(intern):
        return LEAD
    if intern.staff_role == 'pm' and intern.staff_status == 'active':
        return STAFF_PM
    if intern.internship_requests.filter(kind=InternshipRequest.Kind.ENROLL,
                                         status=InternshipRequest.Status.PENDING).exists():
        return PENDING
    if intern.status == InternStatus.PAUSED:
        return FROZEN
    if intern.status in (InternStatus.EXPELLED, InternStatus.CANCELLED, InternStatus.DROPPED,
                         InternStatus.EMPLOYED):
        return STOPPED
    if freeze.open_return(intern) is not None:
        return CHOICE
    from apps.dashboard.graduation import is_graduate
    from apps.teams.models import TeamMember

    if not TeamMember.objects.filter(intern=intern, status=TeamMember.Status.ACTIVE).exists() and is_graduate(intern.pk):
        if intern.graduate_status == 'waiting' or intern.resume_bank_status:
            return GRADUATE_WAITING  # решение уже принято — ждёт проект или проверку резюме
        return GRADUATE_CHOICE
    return ACTIVE


def buttons_for(intern, chat_id=None) -> list[list[str]]:
    from apps.dashboard.graduation import is_graduate
    from apps.interns.discipline import leave_allowed

    state = state_of(intern, chat_id)
    if state == GUEST:
        return [[START], [ALREADY], [HELP]]
    if state == PENDING:
        return [[REQUEST_STATUS], [HELP]]
    if state == FROZEN:
        return [[RETURN], [MY_STATUS, HELP]]
    if state == CHOICE:
        return [[AVAILABLE], [MY_STATUS, HELP]]
    if state == STOPPED:
        return [[MY_STATUS, HELP]]
    if state == GRADUATE_CHOICE:
        return [[CONTINUE, FINISH], [AVAILABLE], [MY_STATUS, HELP]]
    if state == GRADUATE_WAITING:
        return [[AVAILABLE], [MY_STATUS, HELP]]
    if state == LEAD:
        return [[MY_PROJECTS, REVIEWS], [LEAD_REPORTS, MY_STATUS], [HELP]]
    if state == STAFF_PM:
        return [[MY_PROJECTS, PM_REPORT], [MY_STATUS, HELP]]
    rows = [[MY_PROJECTS, MY_TASKS], [SUBMIT, MY_STATUS]]
    extra = []
    if is_active_pm(intern):
        extra.append(PM_REPORT)  # стажёр направления PM — назначенный ПМ проекта
    if is_graduate(intern.pk):
        extra.append(AVAILABLE)
        if leave_allowed(intern):
            extra.append(LEAVE)
    if extra:
        rows.append(extra)
    from apps.interns.freeze import FREEZABLE

    rows.append([FREEZE] if intern.status in FREEZABLE else [])
    rows.append([HELP])
    return [row for row in rows if row]


def menu(intern, chat_id=None) -> types.ReplyKeyboardMarkup:
    return keyboard(buttons_for(intern, chat_id))


def allowed_labels(intern, chat_id=None) -> set[str]:
    return {label for row in buttons_for(intern, chat_id) for label in row}

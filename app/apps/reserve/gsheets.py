"""Выгрузка резерва кадров в общую Google-таблицу.

В таблице на каждое направление свой лист («Список стажеров Backend» и
так далее) — так резерв и сортируется по направлениям. Мы заполняем в
листе только строки людей: шапка, нумерация, выпадающие списки и
оформление остаются как есть.

Без ключа сервисного аккаунта модуль молча ничего не делает — платформа
не должна падать из-за внешней таблицы.
"""
import logging
import re
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

# Одна выгрузка на запрос: листы всё равно переписываются целиком.
_pending = threading.local()

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
API = 'https://sheets.googleapis.com/v4/spreadsheets'

# Шапка занимает первые две строки листа, люди начинаются с третьей.
FIRST_DATA_ROW = 3
# Пишем колонки B–H: номера в A проставлены в таблице заранее.
DATA_RANGE = 'B{start}:H{end}'
# Сколько строк чистим перед записью — с запасом, чтобы не оставался хвост.
CLEAR_ROWS = 500
# Пишем значения как есть: иначе Google Sheets считает «+996 …» формулой
# и показывает #ERROR!, а у «0999…» съедает ведущий ноль.
VALUE_INPUT = 'RAW'

# Колонки листа: ФИО, Направление, Проекты которые делал (D+E — объединены),
# Конец стажировки, Номер телефона, TG username.
COLUMNS = ['ФИО', 'Направление', 'Проекты', '', 'Конец стажировки', 'Телефон', 'TG']

# Направление кандидата → лист. Лист узнаём по его названию, поэтому
# держим для каждого списка слова, которые в этом названии встречаются.
TAB_KEYWORDS = [
    ('backend', ('backend', 'бэкенд', 'бекенд')),
    ('frontend', ('frontend', 'фронтенд')),
    ('mobile', ('mobile', 'мобил', 'flutter', 'android', 'ios')),
    ('uxui', ('uxui', 'ux/ui', 'ux', 'ui', 'дизайн', 'design')),
    ('qa', ('qa', 'тест', 'test')),
    ('pm', ('pm', 'менедж', 'project manager')),
]


def is_configured() -> bool:
    """Есть ли всё, чтобы писать в таблицу."""
    import os

    from apps.common.safety import is_protected

    path = getattr(settings, 'GOOGLE_SHEETS_CREDENTIALS_FILE', '')
    sheet = getattr(settings, 'RESERVE_SHEET_ID', '')
    if is_protected(sheet):
        # Рабочая таблица 1.0: из 2.0 в неё не пишем ни при каких настройках.
        logger.warning('RESERVE_SHEET_ID указывает на таблицу 1.0 — выгрузка отключена.')
        return False
    return bool(path and sheet and os.path.exists(path))


def direction_of(candidate) -> str:
    """Направление кандидата так, как его писать в таблице."""
    if candidate.specialization_id and candidate.specialization:
        return candidate.specialization.name
    if candidate.direction_other:
        return candidate.direction_other
    if candidate.study_specialization_id and candidate.study_specialization:
        return candidate.study_specialization.name
    return ''


def tab_key(text: str) -> str:
    """Ключ направления по названию листа или направления кандидата."""
    lowered = (text or '').lower()
    for key, words in TAB_KEYWORDS:
        if any(word in lowered for word in words):
            return key
    return ''


def projects_of(candidate) -> str:
    """Проекты, на которых человек работал у нас — колонка «Проекты которые делал»."""
    if not candidate.intern_id:
        return ''
    names = []
    for member in candidate.intern.team_memberships.select_related('project').all():
        # Участник может висеть без проекта — такие записи пропускаем.
        if member.project_id is None:
            continue
        name = member.project.name
        if name not in names:
            names.append(name)
    return ', '.join(names)


def candidates_for_sheet():
    """Кого выгружаем: только стажёров.

    Старый резерв — это сотрудники (тимлиды, анкеты), таблица не про них.
    """
    from apps.reserve.models import CandidatePool, ReserveCandidate

    return ReserveCandidate.objects.filter(
        is_archived=False, pool=CandidatePool.INTERN,
    ).select_related('specialization', 'study_specialization', 'intern')


def rows_by_direction(candidates=None) -> dict:
    """Ключ направления → строки листа, отсортированные внутри направления.

    Строка — колонки B–H: ФИО, направление, проекты, пустая (D и E в
    таблице объединены), конец стажировки, телефон, telegram.
    """
    groups: dict[str, list] = {}
    people = candidates if candidates is not None else candidates_for_sheet()
    for candidate in people:
        direction = direction_of(candidate)
        key = tab_key(direction)
        if not key:
            continue
        groups.setdefault(key, []).append(candidate)

    result = {}
    for key, people in groups.items():
        people.sort(key=lambda c: (-c.priority, c.full_name.lower()))
        result[key] = [[
            candidate.full_name,
            direction_of(candidate),
            projects_of(candidate),
            '',
            candidate.study_end.strftime('%d.%m.%Y') if candidate.study_end else '',
            candidate.phone or '',
            (candidate.telegram or '').strip(),
        ] for candidate in people]
    return result


def _session():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        settings.GOOGLE_SHEETS_CREDENTIALS_FILE, scopes=SCOPES,
    )
    return AuthorizedSession(creds)


def _tabs(session, sheet_id: str) -> dict:
    """Ключ направления → название листа в таблице."""
    response = session.get(f'{API}/{sheet_id}?fields=sheets.properties.title')
    response.raise_for_status()
    tabs = {}
    for sheet in response.json().get('sheets') or []:
        title = sheet['properties']['title']
        key = tab_key(title)
        if key and key not in tabs:
            tabs[key] = title
    return tabs


def _quote(title: str) -> str:
    """Название листа в адресе диапазона: с пробелами — в кавычках."""
    if re.fullmatch(r'[A-Za-zА-Яа-яЁё0-9_]+', title or ''):
        return title
    return "'" + (title or '').replace("'", "''") + "'"


def push(groups=None) -> int:
    """Переписать листы направлений. Возвращает число выгруженных строк."""
    from apps.common.safety import is_protected

    sheet_id = settings.RESERVE_SHEET_ID
    if is_protected(sheet_id):
        raise RuntimeError('RESERVE_SHEET_ID указывает на таблицу 1.0 — запись запрещена.')
    groups = rows_by_direction() if groups is None else groups
    session = _session()
    tabs = _tabs(session, sheet_id)

    written = 0
    for key, title in tabs.items():
        rows = groups.get(key, [])
        tab = _quote(title)
        last = FIRST_DATA_ROW + CLEAR_ROWS
        # Сначала чистим строки людей: иначе от прошлой выгрузки останется
        # хвост, если кандидатов стало меньше. Шапку и нумерацию не трогаем.
        clear = session.post(
            f'{API}/{sheet_id}/values/{tab}!'
            + DATA_RANGE.format(start=FIRST_DATA_ROW, end=last) + ':clear',
            json={},
        )
        clear.raise_for_status()
        if not rows:
            continue
        update = session.put(
            f'{API}/{sheet_id}/values/{tab}!B{FIRST_DATA_ROW}',
            params={'valueInputOption': VALUE_INPUT},
            json={'values': rows},
        )
        update.raise_for_status()
        written += len(rows)

    missing = set(groups) - set(tabs)
    if missing:
        logger.warning(
            'Резерв кадров: в таблице нет листов для направлений %s',
            ', '.join(sorted(missing)),
        )
    return written


def sync(reason: str = '') -> bool:
    """Безопасная выгрузка: ошибки внешней таблицы только логируем."""
    if not is_configured():
        return False
    try:
        count = push()
    except Exception:
        logger.exception('Резерв кадров: не удалось обновить Google-таблицу (%s)', reason)
        return False
    logger.info('Резерв кадров: в Google-таблицу выгружено %s строк (%s)', count, reason)
    return True


def sync_later(reason: str = '') -> None:
    """Выгрузка после коммита транзакции, чтобы в таблицу попали свежие данные.

    Листы переписываются целиком, поэтому на пачку изменений (например,
    перетаскивание порядка) хватает одной выгрузки. Работу отдаём Celery;
    если брокер недоступен, пишем таблицу на месте — медленнее, но данные
    не расходятся.
    """
    if not is_configured() or getattr(_pending, 'scheduled', False):
        return
    from django.db import transaction

    _pending.scheduled = True

    def _run():
        _pending.scheduled = False
        from apps.reserve.tasks import sync_reserve_sheet

        try:
            sync_reserve_sheet.delay(reason)
        except Exception:
            logger.warning('Резерв кадров: Celery недоступен, пишу таблицу на месте')
            sync(reason)

    transaction.on_commit(_run)

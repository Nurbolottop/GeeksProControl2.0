"""Выборки для списка резерва: поиск, фильтры, сортировка."""
from django.db.models import Q

from apps.reserve.models import CandidateStatus, ReserveCandidate, ReserveShareLink

# Подпись → выражение сортировки для ORM
SORT_OPTIONS = [
    ('priority', 'Свой порядок'),
    ('rating', 'По рейтингу'),
    ('created', 'По дате добавления'),
    ('updated', 'По дате обновления'),
    ('name', 'По ФИО'),
]
SORT_EXPRESSIONS = {
    # пока порядок не задавали, приоритет у всех нулевой и список
    # выглядит как раньше — по рейтингу
    'priority': ('-priority', '-rating', 'full_name'),
    'rating': ('-rating', 'full_name'),
    'created': ('-created_at',),
    'updated': ('-updated_at',),
    'name': ('full_name',),
}
# Перетаскивать строки можно только в этом режиме — в остальных
# порядок задаёт сортировка, и ручная перестановка ничего не значит
DRAGGABLE_SORT = 'priority'

RATING_OPTIONS = [('9', 'от 9'), ('8', 'от 8'), ('7', 'от 7'), ('6', 'от 6')]

READINESS_OPTIONS = [
    ('looking', 'Ищет работу'),
    ('not_looking', 'Не ищет работу'),
    ('internship', 'Готов(а) к стажировке'),
    ('relocate', 'Готов(а) к переезду'),
]


def candidates(params) -> tuple:
    """Отфильтрованный список кандидатов и применённая сортировка."""
    qs = (
        ReserveCandidate.objects.active()
        .select_related('specialization', 'training_group')
    )
    search = params.get('q', '').strip()
    if search:
        qs = qs.filter(
            Q(full_name__icontains=search)
            | Q(phone__icontains=search)
            | Q(email__icontains=search)
            | Q(telegram__icontains=search),
        )
    specialization = params.get('specialization')
    if specialization == 'none':
        qs = qs.filter(specialization__isnull=True)
    elif specialization:
        qs = qs.filter(specialization_id=specialization)
    if params.get('level'):
        qs = qs.filter(geekspro_level=params['level'])
    if params.get('skill'):
        qs = qs.filter(skills__icontains=params['skill'].strip())
    if params.get('city'):
        qs = qs.filter(city=params['city'])
    if params.get('work_format'):
        qs = qs.filter(work_format=params['work_format'])
    if params.get('employment'):
        qs = qs.filter(employment_type=params['employment'])
    if params.get('status'):
        qs = qs.filter(status=params['status'])
    readiness = params.get('readiness')
    if readiness == 'looking':
        qs = qs.filter(is_looking_for_job=True)
    elif readiness == 'not_looking':
        qs = qs.filter(is_looking_for_job=False)
    elif readiness == 'internship':
        qs = qs.filter(ready_for_internship=True)
    elif readiness == 'relocate':
        qs = qs.filter(ready_to_relocate=True)
    rating = params.get('rating', '')
    if rating.isdigit():
        qs = qs.filter(rating__gte=int(rating))

    sort = params.get('sort', DRAGGABLE_SORT)
    if sort not in SORT_EXPRESSIONS:
        sort = DRAGGABLE_SORT
    return qs.order_by(*SORT_EXPRESSIONS[sort]), sort


# Основные фильтры живут в строке поиска, остальные — под кнопкой «Ещё»
EXTRA_FILTERS = ['level', 'skill', 'city', 'work_format', 'employment', 'readiness', 'rating']


def extra_filters_used(params) -> int:
    """Сколько дополнительных фильтров сейчас включено."""
    return sum(1 for key in EXTRA_FILTERS if params.get(key))


def any_filter_used(params) -> bool:
    return bool(
        params.get('q') or params.get('specialization') or params.get('status')
        or extra_filters_used(params),
    )


def cities() -> list[str]:
    return list(
        ReserveCandidate.objects.active().exclude(city='')
        .values_list('city', flat=True).distinct().order_by('city'),
    )


# Кандидат «в резерве» — доступен для рекомендации; «в работе» — уже
# кому-то предложен; остальные статусы в сводке не считаем занятыми.
AVAILABLE_STATUSES = [CandidateStatus.RESERVE]
IN_PROGRESS_STATUSES = [
    CandidateStatus.PROPOSED, CandidateStatus.INTERVIEW, CandidateStatus.OFFER,
]
NEW_STATUSES = [CandidateStatus.NEW, CandidateStatus.REVIEW]


def summary_by_specialization() -> list[dict]:
    """По каждому направлению: сколько кандидатов и в какой они стадии."""
    from apps.training.models import Specialization

    counters = {}
    rows_by_spec = (
        ReserveCandidate.objects.active()
        .values_list('specialization_id', 'status')
    )
    for spec_id, status in rows_by_spec:
        counts = counters.setdefault(
            spec_id, {'total': 0, 'new': 0, 'available': 0, 'in_progress': 0, 'employed': 0},
        )
        counts['total'] += 1
        if status in NEW_STATUSES:
            counts['new'] += 1
        elif status in AVAILABLE_STATUSES:
            counts['available'] += 1
        elif status in IN_PROGRESS_STATUSES:
            counts['in_progress'] += 1
        elif status == CandidateStatus.EMPLOYED:
            counts['employed'] += 1

    rows = []
    for spec in Specialization.objects.all():
        counts = counters.pop(spec.pk, None) or {
            'total': 0, 'new': 0, 'available': 0, 'in_progress': 0, 'employed': 0,
        }
        rows.append({'specialization': spec, 'key': str(spec.pk), **counts})
    for spec_id, counts in counters.items():
        # кандидаты без направления — тоже строка, иначе их не найти
        rows.append({'specialization': None, 'key': 'none', **counts})
    rows.sort(key=lambda row: (-row['total'], str(row['specialization'] or 'я')))
    return rows


def summary_totals(rows) -> dict:
    keys = ('total', 'new', 'available', 'in_progress', 'employed')
    return {key: sum(row[key] for row in rows) for key in keys}


def share_collections(params) -> list:
    """Действующие подборки для работодателей над списком кандидатов.

    Если список отфильтрован по направлению, показываем только подборки,
    где есть кандидаты этого направления, — иначе подборка «Backend»
    видна и на странице Frontend и её легко отправить не туда. Каждой
    подборке подписываем направления и имена, чтобы было видно, что внутри.
    """
    qs = (
        ReserveShareLink.objects.filter(is_active=True, candidate__isnull=True)
        .prefetch_related('candidates__specialization')
    )
    specialization = params.get('specialization')
    if specialization == 'none':
        qs = qs.filter(candidates__specialization__isnull=True).distinct()
    elif specialization:
        qs = qs.filter(candidates__specialization_id=specialization).distinct()
    links = []
    for link in qs[:20]:
        if not link.is_open:
            continue
        members = [c for c in link.candidates.all() if not c.is_archived]
        link.size = len(members)
        link.directions = sorted({c.direction for c in members})
        link.names = [c.full_name for c in members]
        links.append(link)
    return links


def group_by_direction(candidates) -> list[tuple[str, list]]:
    """Кандидаты подборки по направлениям — в порядке их названий."""
    no_direction = 'Без направления'
    groups = {}
    for candidate in candidates:
        direction = candidate.direction if candidate.direction != '—' else no_direction
        groups.setdefault(direction, []).append(candidate)
    return sorted(
        groups.items(), key=lambda item: (item[0] == no_direction, item[0].lower()),
    )

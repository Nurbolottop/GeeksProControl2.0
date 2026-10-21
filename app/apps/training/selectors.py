"""Выборки для IT-академии.

Академия сообщает только направление, номер группы, даты и численность —
и показываем мы это в том же виде, в каком она присылает:

    🎨 UX/UI
    * 39 группа — старт: 09.06.2026 · конец: 06.10.2026 — 4 студента
"""
import datetime

from django.utils import timezone

from apps.training.models import BRANCHES, GroupStatus, Specialization, TrainingGroup

# Порядок и значки — как в сообщении академии
DIRECTION_ORDER = ['UX/UI', 'Frontend', 'Backend', 'Testing/QA', 'Mobile']
DIRECTION_ICONS = {
    'UX/UI': '🎨', 'Frontend': '💻', 'Backend': '⚙️', 'Mobile': '📱',
    'Testing/QA': '🧪', 'DevOps': '🛠', 'PM': '📋',
}
# Как направление называет академия, если у нас оно называется иначе
ACADEMY_NAMES = {'UX/UI': 'Design', 'Mobile': 'Flutter'}


def plural(number: int, one: str, few: str, many: str) -> str:
    """1 студент, 2 студента, 5 студентов."""
    tail = number % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def students_text(group: TrainingGroup) -> str:
    """Численность так, как её прислали: «5–7 студентов», «пока неизвестно»."""
    if group.students_note:
        return group.students_note
    if group.students_count is None:
        return 'количество студентов пока неизвестно'
    count = group.students_count
    return f'{count} {plural(count, "студент", "студента", "студентов")}'


NO_BRANCH = 'none'


def live_groups(branch: str | None = None):
    """Все группы, кроме не состоявшихся; `branch` — только этого филиала.

    `NO_BRANCH` — группы, у которых филиал не указан.
    """
    qs = (
        TrainingGroup.objects.exclude(status=GroupStatus.CANCELLED)
        .select_related('specialization')
    )
    if branch == NO_BRANCH:
        qs = qs.filter(branch='')
    elif branch:
        qs = qs.filter(branch=branch)
    return qs


def current_groups(branch: str | None = None, today: datetime.date | None = None) -> list:
    """Группы, которые ещё учатся или набираются."""
    today = today or timezone.localdate()
    return [
        group for group in live_groups(branch)
        if group.stage_by_dates(today) != GroupStatus.GRADUATED
    ]


def branch_tabs(today: datetime.date | None = None) -> list[dict]:
    """Вкладки филиалов со сводкой: сколько групп и студентов в каждом.

    Вкладка «Филиал не указан» появляется, только если такие группы есть.
    """
    tabs = []
    for value, label in BRANCHES:
        groups = current_groups(value, today)
        tabs.append({'value': value, 'label': label, **_count(groups)})
    unassigned = current_groups(NO_BRANCH, today)
    if unassigned:
        tabs.append({'value': NO_BRANCH, 'label': 'Филиал не указан', **_count(unassigned)})
    return tabs


def _count(groups) -> dict:
    students = sum(group.students_count or 0 for group in groups)
    return {
        'groups': len(groups),
        'students': students,
        'unknown': sum(1 for group in groups if group.students_count is None),
        'groups_word': plural(len(groups), 'группа', 'группы', 'групп'),
        'students_word': plural(students, 'студент', 'студента', 'студентов'),
    }


def academy_list(branch: str | None = None, today: datetime.date | None = None) -> list[dict]:
    """Группы филиала по направлениям — те, что ещё учатся или набираются."""
    by_spec = {}
    groups = sorted(
        current_groups(branch, today),
        key=lambda group: (group.start_date or datetime.date.max, group.number),
    )
    for group in groups:
        by_spec.setdefault(group.specialization_id, []).append(group)

    def order(spec):
        name = spec.name
        return (DIRECTION_ORDER.index(name) if name in DIRECTION_ORDER else len(DIRECTION_ORDER), name)

    result = []
    for spec in sorted(Specialization.objects.all(), key=order):
        own = by_spec.get(spec.pk)
        if not own:
            continue
        result.append({
            'specialization': spec,
            'icon': DIRECTION_ICONS.get(spec.name, '•'),
            'academy_name': ACADEMY_NAMES.get(spec.name, ''),
            'lines': [{'group': group, 'students': students_text(group)} for group in own],
            'students': sum(group.students_count or 0 for group in own),
        })
    return result


def nav_tree(today: datetime.date | None = None) -> list[dict]:
    """Дерево для сайдбара: филиал → направления с актуальными группами.

    Только филиалы, где сейчас есть хоть одна учащаяся/набирающаяся
    группа — иначе разворачивать в меню нечего.
    """
    tree = []
    for value, label in BRANCHES:
        directions = academy_list(value, today)
        if directions:
            tree.append({'value': value, 'label': label, 'directions': directions})
    unassigned = academy_list(NO_BRANCH, today)
    if unassigned:
        tree.append({'value': NO_BRANCH, 'label': 'Филиал не указан', 'directions': unassigned})
    return tree


def academy_totals(branch: str | None = None, today: datetime.date | None = None) -> dict:
    """Итог одной строкой: сколько групп и студентов в филиале."""
    return _count(current_groups(branch, today))

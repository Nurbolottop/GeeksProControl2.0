"""Кто выпускник — только по достоверным историческим событиям (UI-02.1).

Основания выпуска (любое):
  1. «completed» — человек был активным участником проекта в момент его
     ОФИЦИАЛЬНОГО завершения (статус проекта «Завершён»): участие закрыто
     при закрытии проекта (причина «Проект завершён» не раньше даты
     закрытия) или всё ещё активно, и на дату закрытия он не был заморожен;
  2. «confirmed» — сохранённая отметка выпуска: Intern.graduate_status
     заполнен (её ставит система при выпуске; при новом назначении 1.0 её
     сбрасывала — поэтому одного её отсутствия для «не выпускник» мало);
  3. «promoted» — повышение до штатного тимлида (запись в журнале или
     текущая должность «Тимлид»): по правилам 2.0 даёт статус выпускника.

НЕ основания: повторная стажировка (internship_attempt > 1) — человек мог
быть отчислен и вернуться; выход до закрытия; заморозка на дату закрытия.

Неоднозначные записи в выпускники не попадают, а идут в `review` — их
сверяет руководитель (manage.py graduate_reconciliation). Ничего в БД
этот модуль не меняет.
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import dataclass, field

from django.db.models import Q

from apps.interns.models import GraduateStatus, Intern, InternStatus, Position
from apps.projects.models import ProjectStatus
from apps.teams.models import TeamMember

EARLY_EXIT = (
    TeamMember.LeftReason.SELF,
    TeamMember.LeftReason.NOT_FIT,
    TeamMember.LeftReason.UNRESPONSIVE,
    TeamMember.LeftReason.FROZEN,  # заморозка = выход из всех проектов (UI-03B)
    TeamMember.LeftReason.EXPELLED,  # отчисление (UI-04B)
    TeamMember.LeftReason.CANCELLED,  # добровольная отмена (UI-04B)
    TeamMember.LeftReason.LEFT_PROJECT,  # выпускник вышел из одного проекта до закрытия (UI-04C)
    # этап 6: выпуск при повышении — основание «promoted»; кадровые выходы — не выпуск
    TeamMember.LeftReason.PROMOTED,
    TeamMember.LeftReason.REPLACED,
    TeamMember.LeftReason.DISMISSED,
    TeamMember.LeftReason.EMPLOYED_STUDIO,
)
PROMOTED_ACTION = 'Повышен(а) до тимлида'
FREEZE_LABEL = InternStatus.PAUSED.label


@dataclass
class Graduation:
    ids: set[int] = field(default_factory=set)
    first_dates: dict[int, datetime.date] = field(default_factory=dict)
    evidence: dict[int, list[tuple[str, datetime.date | None]]] = field(default_factory=dict)
    review: list[dict] = field(default_factory=list)
    # Проекты, на официальном завершении которых человек выпустился:
    # intern_id → [(project_id, название, дата закрытия)]
    projects: dict[int, list[tuple[int, str, datetime.date]]] = field(default_factory=dict)


def _freeze_events(intern_ids) -> dict[int, list[tuple[datetime.date, bool]]]:
    """Журнал: (дата, True=заморозка / False=разморозка) по людям, по порядку."""
    from django.utils import timezone

    from apps.audit.models import AuditLog

    events: dict[int, list] = defaultdict(list)
    if not intern_ids:
        return events
    logs = AuditLog.objects.filter(
        object_type='Intern', object_id__in=[str(i) for i in intern_ids],
    ).filter(
        Q(action='Стажировка заморожена') | Q(action='Стажировка возобновлена')
        | Q(action='Статус изменён', new_value=FREEZE_LABEL)
        | Q(action='Статус изменён', old_value=FREEZE_LABEL),
    ).order_by('created_at').values_list('object_id', 'action', 'new_value', 'created_at')
    for object_id, action, new_value, created_at in logs:
        is_freeze = action == 'Стажировка заморожена' or (action == 'Статус изменён' and new_value == FREEZE_LABEL)
        events[int(object_id)].append((timezone.localtime(created_at).date(), is_freeze))
    return events


def frozen_on(events, day: datetime.date, currently_paused: bool) -> bool | None:
    """Был ли заморожен в этот день. None — нельзя установить по журналу."""
    before = [frozen for event_day, frozen in events if event_day <= day]
    if before:
        return before[-1]
    if events:
        # Первое событие после этой даты: разморозка → до неё был заморожен
        return not events[0][1]
    return None if currently_paused else False


def compute(intern_ids=None, exclude_project_id=None) -> Graduation:
    """intern_ids — посчитать только для этих людей (для проверок назначения).

    exclude_project_id — не учитывать этот проект (кто был выпускником ДО
    его закрытия: процедура закрытия так отличает новых выпускников).
    """
    from django.utils import timezone

    from apps.audit.models import AuditLog

    result = Graduation()
    evidence: dict[int, list] = defaultdict(list)
    review: dict[tuple, dict] = {}
    names = {}

    def flag(intern_id, name, reason, project=''):
        review.setdefault((intern_id, reason), {
            'intern_id': intern_id, 'name': name, 'reason': reason, 'project': project,
        })

    # 2. Сохранённая отметка выпуска
    def scoped(qs, field='pk'):
        return qs if intern_ids is None else qs.filter(**{f'{field}__in': list(intern_ids)})

    def scoped_log(qs):
        return qs if intern_ids is None else qs.filter(object_id__in=[str(i) for i in intern_ids])

    for pk, name in scoped(Intern.objects.filter(graduate_status__in=GraduateStatus.values)).values_list('pk', 'full_name'):
        evidence[pk].append(('confirmed', None))
        names[pk] = name
    # Отметку выпуска 1.0 снимала при новом назначении — но в журнале остался
    # след «Статус выпускника снят»: это сохранённое подтверждение выпуска.
    for object_id in scoped_log(AuditLog.objects.filter(
        object_type='Intern', action='Статус выпускника снят',
    )).values_list('object_id', flat=True):
        if object_id.isdigit():
            evidence[int(object_id)].append(('confirmed', None))

    # 3. Повышение до штатного тимлида
    for object_id, created_at in scoped_log(AuditLog.objects.filter(
        object_type='Intern', action=PROMOTED_ACTION,
    )).values_list('object_id', 'created_at'):
        if object_id.isdigit():
            evidence[int(object_id)].append(('promoted', timezone.localtime(created_at).date()))
    for pk in scoped(Intern.objects.filter(position=Position.TEAM_LEAD)).values_list('pk', flat=True):
        if not any(kind == 'promoted' for kind, _ in evidence[pk]):
            evidence[pk].append(('promoted', None))

    # 1. Активен в момент официального завершения проекта
    rows = list(
        scoped(TeamMember.objects.filter(
            intern__isnull=False, project__status=ProjectStatus.COMPLETED, project__is_archived=False,
        ).exclude(project_id=exclude_project_id), 'intern_id').values_list(
            'intern_id', 'intern__full_name', 'intern__status', 'status', 'left_reason', 'left_at',
            'project__name', 'project__actual_end_date', 'project_id',
        ),
    )
    projects: dict[int, list] = defaultdict(list)
    freezes = _freeze_events({row[0] for row in rows})
    for intern_id, name, intern_status, status, reason, left_at, project, end, project_id in rows:
        names[intern_id] = name
        if reason in EARLY_EXIT:
            continue  # ушёл / отчислен до закрытия — не выпускник
        if status == TeamMember.Status.ACTIVE:
            day = end
        elif reason == TeamMember.LeftReason.PROJECT_ENDED:
            if end and left_at and left_at < end:
                flag(intern_id, name, 'снят с причиной «Проект завершён» до официального закрытия', project)
                continue
            day = end or left_at
        else:
            flag(intern_id, name, 'вышел из завершённого проекта без указания причины', project)
            continue
        if day is None:
            flag(intern_id, name, 'у завершённого проекта нет даты закрытия', project)
            continue
        frozen = frozen_on(freezes.get(intern_id, []), day, intern_status == InternStatus.PAUSED)
        if frozen is None:
            flag(intern_id, name, 'сейчас заморожен, дата заморозки неизвестна', project)
            continue
        if frozen:
            continue  # заморожен на дату закрытия — не выпускник
        evidence[intern_id].append(('completed', day))
        projects[intern_id].append((project_id, project, day))

    # «Проект завершён» у проекта, который официально не закрыт
    for intern_id, name, project in scoped(TeamMember.objects.filter(
        intern__isnull=False, left_reason=TeamMember.LeftReason.PROJECT_ENDED,
        project__status__in=[ProjectStatus.ACTIVE, ProjectStatus.PAUSED],
    ), 'intern_id').values_list('intern_id', 'intern__full_name', 'project__name'):
        flag(intern_id, name, 'снят с причиной «Проект завершён», но проект официально не закрыт', project)

    # Повторная стажировка без подтверждённого выпуска
    for pk, name in scoped(Intern.objects.filter(internship_attempt__gt=1)).values_list('pk', 'full_name'):
        flag(pk, name, 'повторная стажировка без подтверждённого выпуска')

    result.evidence = dict(evidence)
    result.projects = {pk: sorted(items, key=lambda item: item[2]) for pk, items in projects.items()}
    result.ids = set(evidence)
    for pk, items in evidence.items():
        dated = [day for _kind, day in items if day]
        if dated:
            result.first_dates[pk] = min(dated)
    # Уже выпускник по другому основанию — сверять нечего
    result.review = sorted(
        (item for (pk, _), item in review.items() if pk not in result.ids),
        key=lambda item: item['name'],
    )
    return result


def is_graduate(intern_id: int) -> bool:
    return intern_id in compute([intern_id]).ids

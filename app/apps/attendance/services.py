"""Собрания группы и табель посещаемости."""
import calendar
import datetime

from django.db import transaction
from django.utils import timezone

from apps.attendance.models import (
    Attendance, GroupMeeting, MeetingKind, WorkScore,
)
from apps.teams.models import TeamMember, TeamRole

# Посещаемость и «Активность» ставят только стажёрам команды — ПМ и
# тимлид не студенты, их в табеле быть не должно (ТЗ, реплика
# пользователя: «пусть в табеле... не будут выходить ПМ и тимлиды»).
ATTENDANCE_EXCLUDED_ROLES = (TeamRole.PROJECT_MANAGER, TeamRole.TEAM_LEAD)


def attendance_eligible_members(group):
    """Участники группы, которых можно отмечать/оценивать — без ПМ,
    тимлида, стажёров, чья стажировка сейчас заморожена, и тех, кто уже
    вышел из команды.

    Без фильтра по status=ACTIVE сюда попадали и записи о вышедших
    участниках — а если у человека к тому же было две записи в одной
    группе (вышедшая + текущая, дубль), get_object_or_404() в
    meeting_mark_toggle/meeting_score падал с MultipleObjectsReturned
    (500) при попытке тимлида его отметить/оценить.
    """
    from apps.interns.models import InternStatus

    return group.members.filter(status=TeamMember.Status.ACTIVE).exclude(
        role__in=ATTENDANCE_EXCLUDED_ROLES,
    ).exclude(intern__status=InternStatus.PAUSED)


def month_bounds(year: int, month: int) -> tuple[datetime.date, datetime.date]:
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, 1), datetime.date(year, month, last_day)


def default_host(group, kind: str):
    """Кто проводит собрание: PM или тимлид группы — по виду собрания."""
    role = 'team_lead' if kind == MeetingKind.LEAD_INTERNS else 'pm'
    member = group.members.filter(
        role=role, status='active', intern__isnull=False,
    ).select_related('intern').first()
    return member.intern if member else None


@transaction.atomic
def create_meeting(
    group, kind: str, date: datetime.date, host=None, topic: str = '',
    direction: str = '',
) -> GroupMeeting | None:
    """Создаёт одно собрание на дату. Ведущий подставляется по виду собрания.

    `direction` — роль в команде, для которой собрание: тимлид собирает
    своё направление, и у других направлений в этот день может быть своё
    собрание. Пусто — общее собрание команды.

    Возвращает None, если такое собрание уже есть.
    """
    meeting, is_new = GroupMeeting.objects.get_or_create(
        group=group, kind=kind, date=date, direction=direction,
        defaults={'host': host or default_host(group, kind), 'topic': topic},
    )
    return meeting if is_new else None


def build_sheet(group, year: int, month: int) -> dict:
    """Табель: строки — люди группы, колонки — собрания месяца."""
    first, last = month_bounds(year, month)
    meetings = list(
        group.meetings.filter(date__gte=first, date__lte=last)
        .order_by('date', 'kind'),
    )
    members = [
        member for member in attendance_eligible_members(group).select_related(
            'intern__specialization',
        ).order_by('role', 'intern__full_name')
        if member.intern_id
    ]

    marks = {
        (mark.intern_id, mark.meeting_id): mark
        for mark in Attendance.objects.filter(meeting__in=meetings)
    }
    scores = {
        (item.intern_id, item.meeting_id): item.score
        for item in WorkScore.objects.filter(meeting__in=meetings)
    }

    rows = []
    for member in members:
        cells, attended, marked = [], 0, 0
        person_scores = []
        for meeting in meetings:
            mark = marks.get((member.intern_id, meeting.pk))
            if mark:
                marked += 1
                if mark.is_attended:
                    attended += 1
            score = scores.get((member.intern_id, meeting.pk))
            if score is not None:
                person_scores.append(score)
            cells.append({
                'meeting': meeting,
                'status': mark.status if mark else '',
                'score': score,
            })
        rows.append({
            'member': member,
            'intern': member.intern,
            'cells': cells,
            'marked': marked,
            'attended': attended,
            'rate': round(attended / marked * 100) if marked else None,
            'scores': person_scores,
            'activity': (
                round(sum(person_scores) / len(person_scores), 1)
                if person_scores else None
            ),
        })

    total_marked = sum(row['marked'] for row in rows)
    total_attended = sum(row['attended'] for row in rows)
    all_scores = [value for row in rows for value in row['scores']]

    # Сводка по видам: сколько собраний всего и сколько уже проведено
    by_kind: dict[str, dict] = {}
    for meeting in meetings:
        entry = by_kind.setdefault(meeting.kind, {
            'label': meeting.get_kind_display(), 'total': 0, 'held': 0,
        })
        entry['total'] += 1
        entry['held'] += int(meeting.status == GroupMeeting.Status.HELD)

    return {
        'meetings': meetings,
        'rows': rows,
        'rate': round(total_attended / total_marked * 100) if total_marked else None,
        'marked': total_marked,
        'activity': (
            round(sum(all_scores) / len(all_scores), 1) if all_scores else None
        ),
        'by_kind': list(by_kind.values()),
    }


def previous_scores(meeting) -> dict[int, int]:
    """Баллы, выставленные на прошлом собрании того же вида."""
    previous = meeting.previous
    if previous is None:
        return {}
    return {
        item.intern_id: item.score
        for item in WorkScore.objects.filter(meeting=previous)
    }


def score_row(meeting, intern, score=None, comment: str = '',
              previous: int | None = None) -> dict:
    """Данные одной строки оценки: балл, прошлый балл и изменение."""
    delta = None
    if score is not None and previous is not None:
        delta = score - previous
    return {
        'intern': intern,
        'member': meeting.group.members.filter(intern=intern).first(),
        'score': score,
        'score_comment': comment,
        'previous': previous,
        'delta': delta,
        'segments': score_segments(score),
        'level': score_level(score),
    }


def score_level(score) -> str:
    """Уровень балла для подсветки: high / mid / low / empty."""
    if score is None:
        return 'empty'
    if score >= 8:
        return 'high'
    return 'mid' if score >= 5 else 'low'


def score_segments(score) -> list[dict]:
    """Полоса шкалы: 10 делений, залиты до выставленного балла."""
    return [
        {'value': value, 'on': score is not None and value <= score}
        for value in range(1, WorkScore.MAX + 1)
    ]


def toggle_mark(meeting, intern, user=None) -> Attendance | None:
    """Переключает отметку: Был → Не был → Опоздал → Уважительная → пусто."""
    mark = Attendance.objects.filter(meeting=meeting, intern=intern).first()
    if mark is None:
        # Первая отметка на собрании — считаем его проведённым
        if meeting.status == GroupMeeting.Status.PLANNED:
            meeting.status = GroupMeeting.Status.HELD
            meeting.save(update_fields=['status', 'updated_at'])
        return Attendance.objects.create(
            meeting=meeting, intern=intern,
            status=Attendance.Status.PRESENT, marked_by=user,
        )
    cycle = Attendance.CYCLE
    index = cycle.index(mark.status) if mark.status in cycle else 0
    if index + 1 < len(cycle):
        mark.status = cycle[index + 1]
        mark.marked_by = user
        mark.save(update_fields=['status', 'marked_by', 'updated_at'])
        return mark
    mark.delete()
    return None


@transaction.atomic
def mark_all_present(meeting, user=None, only_role=None) -> int:
    """Отметить всю активную команду присутствующей на собрании.

    ``only_role`` — сузить до одного направления (например, тимлид
    в своём портале отмечает только свою часть команды).
    """
    created = 0
    members = attendance_eligible_members(meeting.group).filter(
        status='active',
    ).exclude(intern__isnull=True)
    if only_role:
        members = members.filter(role=only_role)
    for member in members:
        _, is_new = Attendance.objects.get_or_create(
            meeting=meeting, intern=member.intern,
            defaults={'status': Attendance.Status.PRESENT, 'marked_by': user},
        )
        created += int(is_new)
    if created and meeting.status == GroupMeeting.Status.PLANNED:
        meeting.status = GroupMeeting.Status.HELD
        meeting.save(update_fields=['status', 'updated_at'])
    return created


def meeting_completion(meeting, members) -> dict:
    """Насколько собрание закрыто для списка участников: у всех ли
    проставлена отметка посещения и оценка активности.

    Используется как валидация у тимлида — новое собрание нельзя
    создать, пока не закрыто предыдущее (посещаемость и оценки
    обязательны; у ПМ марки/оценок больше нет — только просмотр).
    """
    intern_ids = [member.intern_id for member in members if member.intern_id]
    total = len(intern_ids)
    marked = Attendance.objects.filter(
        meeting=meeting, intern_id__in=intern_ids,
    ).values('intern_id').distinct().count()
    scored = WorkScore.objects.filter(
        meeting=meeting, intern_id__in=intern_ids,
    ).values('intern_id').distinct().count()
    return {
        'total': total, 'marked': marked, 'scored': scored,
        'complete': total == 0 or (marked >= total and scored >= total),
    }


def upcoming_meetings(group, limit: int = 5):
    today = timezone.localdate()
    return group.meetings.filter(date__gte=today).order_by('date')[:limit]

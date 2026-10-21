"""Общий экран табеля: собрания и посещаемость по всем группам."""
import datetime

from django.utils import timezone

from apps.attendance.models import Attendance, GroupMeeting, WorkScore
from apps.flows.models import Group


def week_bounds(day: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Понедельник — воскресенье недели, в которую попадает дата."""
    start = day - datetime.timedelta(days=day.weekday())
    return start, start + datetime.timedelta(days=6)


def groups_summary(first: datetime.date, last: datetime.date) -> list[dict]:
    """По каждой группе: собрания периода, проведено, посещаемость."""
    groups = (
        Group.objects.select_related('flow', 'project')
        .prefetch_related('members')
        .order_by('flow__number', 'number')
    )
    meetings = GroupMeeting.objects.filter(date__gte=first, date__lte=last)
    marks = Attendance.objects.filter(meeting__in=meetings).select_related('meeting')
    scores = WorkScore.objects.filter(meeting__in=meetings).select_related('meeting')

    by_group_meetings: dict[int, list] = {}
    for meeting in meetings:
        by_group_meetings.setdefault(meeting.group_id, []).append(meeting)
    by_group_marks: dict[int, list] = {}
    for mark in marks:
        by_group_marks.setdefault(mark.meeting.group_id, []).append(mark)
    by_group_scores: dict[int, list] = {}
    for score in scores:
        by_group_scores.setdefault(score.meeting.group_id, []).append(score.score)

    rows = []
    for group in groups:
        group_meetings = by_group_meetings.get(group.pk, [])
        group_marks = by_group_marks.get(group.pk, [])
        held = sum(
            1 for meeting in group_meetings
            if meeting.status == GroupMeeting.Status.HELD
        )
        attended = sum(1 for mark in group_marks if mark.is_attended)
        group_scores = by_group_scores.get(group.pk, [])
        rows.append({
            'group': group,
            'people': sum(
                1 for member in group.members.all() if member.status == 'active'
            ),
            'meetings': len(group_meetings),
            'held': held,
            'rate': round(attended / len(group_marks) * 100) if group_marks else None,
            'activity': (
                round(sum(group_scores) / len(group_scores), 1)
                if group_scores else None
            ),
        })
    return rows


def today_meetings() -> list[GroupMeeting]:
    """Собрания на сегодня по всем группам."""
    return list(
        GroupMeeting.objects.filter(date=timezone.localdate())
        .select_related('group', 'group__project', 'group__flow', 'host')
        .order_by('group__flow__number', 'group__number'),
    )


def period_meetings(
    first: datetime.date, last: datetime.date,
) -> list[GroupMeeting]:
    """Все собрания выбранной недели — по дням."""
    return list(
        GroupMeeting.objects.filter(date__gte=first, date__lte=last)
        .select_related('group', 'group__project', 'group__flow', 'host')
        .order_by('date', 'group__flow__number', 'group__number'),
    )

"""Обязательные собрания (этап 9B).

Обязанность — пара «проект + направление», где в эту неделю (пн–вс, Бишкек)
были стажёры этого направления: по истории участий на эту неделю, а не по
сегодняшнему составу (reports.weekly.members_during — та же основа, что у
обязательных отчётов тимлидов этапа 7). Тимлид Frontend в двух проектах —
две обязанности. Встречи ПМ с тимлидами и дополнительные собрания норму не
закрывают.

Статусы:
* no_lead — у направления нет тимлида (кадровая проблема; собраний не выдумываем);
* fulfilled — обязательное собрание проведено (видеосервис или ручная отметка);
* planned — запланировано, неделя идёт; awaiting — время прошло, подтверждения нет;
* not_planned — неделя идёт, обязательное не назначено;
* missed_not_planned — неделя закончилась, обязательного не было;
* missed_not_held — было запланировано, видеосервис прислал события, но собрание
  не состоялось (не было ведущего и подтверждённого участника);
* insufficient — было запланировано, но событий нет: проведено ли — неизвестно.
Пропуск за текущую неделю не ставится до её окончания.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.utils import timezone

from apps.teams.models import TeamRole
from apps.video.models import VideoMeeting

LABELS = {
    'no_lead': 'Нет тимлида направления', 'fulfilled': 'Проведено — норма выполнена',
    'planned': 'Запланировано', 'awaiting': 'Время прошло — нет подтверждения',
    'not_planned': 'Не запланировано', 'missed_not_planned': 'Не выполнено: не назначали',
    'missed_not_held': 'Не выполнено: не состоялось', 'insufficient': 'Недостаточно данных',
}
TONES = {'no_lead': 'danger', 'fulfilled': 'success', 'planned': 'info', 'awaiting': 'warning',
         'not_planned': 'warning', 'missed_not_planned': 'danger', 'missed_not_held': 'danger',
         'insufficient': 'neutral'}
MISSED = ('missed_not_planned', 'missed_not_held')
DIRECTIONS = dict(TeamRole.choices)


@dataclass
class MeetingObligation:
    project: object
    direction: str
    week: object
    status: str
    leads: list = field(default_factory=list)
    meetings: list = field(default_factory=list)
    interns: int = 0

    @property
    def label(self):
        return LABELS[self.status]

    @property
    def tone(self):
        return TONES[self.status]

    @property
    def direction_label(self):
        return DIRECTIONS.get(self.direction, self.direction)

    @property
    def held_manually(self):
        return any(m.status == 'held' and m.held_source == 'manual' for m in self.meetings)


def week_obligations(week, today=None, *, project_ids=None, lead=None) -> list[MeetingObligation]:
    from apps.dashboard.metrics import week_of
    from apps.reports.weekly import members_during
    from apps.teams.rules import DIRECTION_ROLES
    from apps.teams.selectors import staff_intern_ids
    from apps.teams.staff import lead_member_direction

    today = today or timezone.localdate()
    week = week_of(week.start, today)
    staff = staff_intern_ids()
    interns = members_during(week, roles=list(DIRECTION_ROLES)).exclude(intern_id__in=staff)
    leads = members_during(week, roles=[TeamRole.TEAM_LEAD]).select_related('intern__specialization', 'project')
    if project_ids is not None:
        interns, leads = interns.filter(project_id__in=project_ids), leads.filter(project_id__in=project_ids)
    counts = {}
    for project_id, role, intern_id in interns.values_list('project_id', 'role', 'intern_id'):
        counts.setdefault((project_id, role), set()).add(intern_id)
    by_key = {}
    for member in leads:
        by_key.setdefault((member.project_id, lead_member_direction(member)), {})[member.intern_id] = member.intern
    meetings = {}
    for meeting in VideoMeeting.objects.filter(week_start=week.start, kind=VideoMeeting.Kind.REQUIRED).exclude(
            status=VideoMeeting.Status.CANCELLED).prefetch_related('events'):
        meetings.setdefault((meeting.project_id, meeting.direction), []).append(meeting)
    from apps.projects.models import Project

    projects = Project.objects.in_bulk({k[0] for k in counts})
    now = timezone.now()
    result = []
    for key in sorted(counts, key=lambda k: (projects[k[0]].name, k[1])):
        lead_map = by_key.get(key, {})
        if lead is not None and lead.pk not in lead_map:
            continue
        items = meetings.get(key, [])
        if any(m.status == VideoMeeting.Status.HELD for m in items):
            status = 'fulfilled'
        elif not lead_map:
            status = 'no_lead'
        elif not week.complete:
            if not items:
                status = 'not_planned'
            elif all(m.starts_at < now for m in items):
                status = 'awaiting'
            else:
                status = 'planned'
        elif not items:
            status = 'missed_not_planned'
        elif any(m.events.all() for m in items):
            status = 'missed_not_held'
        else:
            status = 'insufficient'
        result.append(MeetingObligation(project=projects[key[0]], direction=key[1], week=week, status=status,
                                        leads=list(lead_map.values()), meetings=items, interns=len(counts[key])))
    return result


def summary(week, today=None) -> dict:
    from apps.video.attendance import compute_many

    items = week_obligations(week, today)
    week = items[0].week if items else week
    count = lambda *s: sum(1 for o in items if o.status in s)  # noqa: E731
    direction_meetings = list(VideoMeeting.objects.filter(
        week_start=week.start, kind__in=[VideoMeeting.Kind.REQUIRED, VideoMeeting.Kind.EXTRA]).exclude(
        status=VideoMeeting.Status.CANCELLED))
    unique, present, invited, no_data = set(), 0, 0, 0
    rows_by_meeting = compute_many(direction_meetings)
    for meeting in direction_meetings:
        for row in rows_by_meeting[meeting.pk]:
            invited += 1
            if row.present:
                present += 1
                unique.add(row.participant.intern_id)
            elif row.value == 'no_data' and meeting.starts_at < timezone.now():
                no_data += 1
    return {
        'obligations': len(items), 'fulfilled': count('fulfilled'), 'fulfilled_manual': sum(
            1 for o in items if o.status == 'fulfilled' and o.held_manually),
        'missed': count(*MISSED) if week.complete else None, 'insufficient': count('insufficient'),
        'no_lead': count('no_lead'), 'planned': count('planned', 'awaiting'), 'not_planned': count('not_planned'),
        'meetings_planned': sum(1 for m in direction_meetings if m.status == VideoMeeting.Status.PLANNED),
        'meetings_held': sum(1 for m in direction_meetings if m.status == VideoMeeting.Status.HELD),
        'unique_participants': len(unique), 'confirmed_attendance': present, 'invited': invited,
        'attendance_no_data': no_data,
        'pm_meetings': VideoMeeting.objects.filter(week_start=week.start, kind=VideoMeeting.Kind.PM_LEADS).exclude(
            status=VideoMeeting.Status.CANCELLED).count(),
        'complete': week.complete,
    }


def list_context(request, scope, week) -> dict:
    from apps.dashboard.metrics import week_of

    week = week or week_of(timezone.localdate())
    lead, project_ids = None, None
    if scope == 'lead':
        lead = getattr(request.user, 'intern_profile', None)
    elif scope == 'pm':
        from apps.teams.models import TeamMember

        intern = getattr(request.user, 'intern_profile', None)
        project_ids = list(TeamMember.objects.filter(intern=intern, role=TeamRole.PROJECT_MANAGER,
                                                     status=TeamMember.Status.ACTIVE).values_list('project_id', flat=True))
    items = week_obligations(week, project_ids=project_ids, lead=lead)
    counts = {key: sum(1 for o in items if o.status == key) for key in LABELS}
    return {'obligations_block': True, 'obligations': items, 'obligation_week': items[0].week if items else week,
            'obligation_counts': [(LABELS[k], str(v), TONES[k]) for k, v in counts.items() if v]}

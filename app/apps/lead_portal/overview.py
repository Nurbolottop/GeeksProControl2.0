"""«Обзор» проекта в портале тимлида: как дела у его направления.

Тимлид отвечает за людей своего направления, поэтому всё считаем по ним:
сколько человек, как ходят на собрания и как работают за последние
четыре недели, кому нужно внимание. Плюс путь проекта, ближайшие
собрания и контакт ПМ — чтобы было понятно, куда идём и с кем говорить.
"""
import datetime

from django.utils import timezone

from apps.attendance.models import Attendance, GroupMeeting, WorkScore
from apps.projects import graphics
from apps.projects.models import ProjectStatus
from apps.teams.models import TeamMember, TeamRole

WINDOW_DAYS = 28
LOW_ATTENDANCE = 70   # %, ниже — стоит поговорить
LOW_ACTIVITY = 5      # из 10


def _days(number: int) -> str:
    number = abs(number)
    if number % 10 == 1 and number % 100 != 11:
        return 'день'
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return 'дня'
    return 'дней'


def lead_overview(project, own_role) -> dict:
    today = timezone.localdate()
    since = today - datetime.timedelta(days=WINDOW_DAYS)

    roadmap = graphics.stage_roadmap(project)
    road_data = graphics.road_data(roadmap)

    members = (
        project.team_members.filter(status=TeamMember.Status.ACTIVE, intern__isnull=False)
        .exclude(role__in=[TeamRole.PROJECT_MANAGER, TeamRole.TEAM_LEAD])
        .select_related('intern__specialization')
    )
    if own_role:
        members = members.filter(role=own_role)
    people = [member.intern for member in members]
    ids = [person.pk for person in people]

    group = getattr(project, 'group', None)
    meetings = GroupMeeting.objects.none()
    if group is not None:
        meetings = group.meetings.all()
    window = meetings.filter(date__gte=since, date__lte=today)

    marks = Attendance.objects.filter(meeting__in=window, intern_id__in=ids)
    scores = WorkScore.objects.filter(meeting__in=window, intern_id__in=ids)
    by_person = {pk: {'marks': 0, 'attended': 0, 'scores': []} for pk in ids}
    for mark in marks:
        by_person[mark.intern_id]['marks'] += 1
        by_person[mark.intern_id]['attended'] += int(mark.is_attended)
    for score in scores:
        by_person[score.intern_id]['scores'].append(score.score)

    total_marks = sum(row['marks'] for row in by_person.values())
    total_attended = sum(row['attended'] for row in by_person.values())
    all_scores = [value for row in by_person.values() for value in row['scores']]
    held = window.filter(status=GroupMeeting.Status.HELD).count()

    attention = []
    for person in people:
        row = by_person[person.pk]
        rate = round(100 * row['attended'] / row['marks']) if row['marks'] else None
        activity = (
            round(sum(row['scores']) / len(row['scores']), 1) if row['scores'] else None
        )
        reasons = []
        if held and not row['marks']:
            reasons.append('нет отметок в табеле')
        if rate is not None and rate < LOW_ATTENDANCE:
            reasons.append(f'посещаемость {rate}%')
        if activity is not None and activity < LOW_ACTIVITY:
            reasons.append(f"активность {str(activity).replace('.', ',')} из 10")
        if reasons:
            attention.append({
                'person': person, 'rate': rate, 'activity': activity,
                'reasons': reasons,
            })

    tiles = [{
        'value': len(people),
        'unit': 'чел.',
        'label': 'моё направление' if own_role else 'в команде',
        'hint': f'добавлено за 4 недели: {members.filter(joined_at__gte=since).count()}',
        'tone': 'teal',
    }]
    if total_marks:
        rate = round(100 * total_attended / total_marks)
        tiles.append({
            'value': rate, 'unit': '%', 'label': 'посещаемость',
            'hint': f'за 4 недели · собраний проведено: {held}',
            'tone': 'green' if rate >= 85 else 'yellow' if rate >= LOW_ATTENDANCE else 'red',
        })
    else:
        tiles.append({
            'text': '—', 'label': 'посещаемость',
            'hint': 'за 4 недели отметок нет', 'tone': 'yellow',
        })
    if all_scores:
        average = round(sum(all_scores) / len(all_scores), 1)
        tiles.append({
            # data-count набегает целым — дробную часть показываем в единицах
            'text': str(average).replace('.', ','), 'unit': 'из 10',
            'label': 'средняя активность',
            'hint': f'оценок за 4 недели: {len(all_scores)}',
            'tone': 'green' if average >= 8 else 'yellow' if average >= LOW_ACTIVITY else 'red',
        })
    else:
        tiles.append({
            'text': '—', 'label': 'средняя активность',
            'hint': 'за 4 недели оценок нет', 'tone': 'violet',
        })
    deadline = project.planned_end_date
    is_finished = project.status != ProjectStatus.ACTIVE
    if is_finished:
        # у закрытого проекта «до дедлайна» уже ничего не значит
        finished_on = project.actual_end_date
        tiles.append({
            'text': '✓' if project.status == ProjectStatus.COMPLETED else '—',
            'label': project.get_status_display().lower(),
            'hint': f'{finished_on:%d.%m.%Y}' if finished_on else 'проект больше не в работе',
            'tone': 'green' if project.status == ProjectStatus.COMPLETED else 'yellow',
        })
    elif deadline:
        left = (deadline - today).days
        tiles.append({
            'value': abs(left), 'unit': _days(left),
            'label': 'до дедлайна' if left >= 0 else 'просрочки',
            'hint': f'{deadline:%d.%m.%Y}',
            'tone': 'red' if left < 0 or left <= 7 else 'yellow' if left <= 21 else 'blue',
        })

    upcoming = list(
        meetings.filter(date__gte=today).select_related('host').order_by('date')[:3],
    )
    unmarked = list(
        meetings.filter(date__lt=today, date__gte=since, status=GroupMeeting.Status.PLANNED)
        .order_by('-date')[:3],
    )

    pm = (
        project.team_members.filter(
            role=TeamRole.PROJECT_MANAGER, status=TeamMember.Status.ACTIVE,
        )
        .select_related('intern', 'user').first()
    )

    return {
        'roadmap': roadmap,
        'road_data': road_data,
        'lead_tiles': tiles,
        'attention': attention,
        'people_count': len(people),
        'upcoming': upcoming,
        'unmarked': unmarked,
        'group': group,
        'pm': pm,
        'is_finished': is_finished,
    }

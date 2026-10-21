"""Карточка стажёра в порталах ПМ и тимлида (UI-03C) — общие данные."""
from django.urls import reverse

from apps.attendance.models import WorkScore
from apps.teams.models import TeamMember


def intern_card_context(project, member, scope) -> dict:
    intern = member.intern
    group = getattr(project, 'group', None)
    scores = list(
        WorkScore.objects.filter(intern=intern, meeting__group=group).select_related('meeting')
        .order_by('-meeting__date') if group else [],
    )
    values = [item.score for item in scores]
    average = round(sum(values) / len(values), 1) if values else None
    detail = 'pm_portal:project_detail' if scope == 'pm' else 'lead_portal:project_detail'
    home = ('Мои проекты', 'pm_portal:dashboard' if scope == 'pm' else 'lead_portal:dashboard')
    tasks = list(project.tasks.active().filter(assignee_id=intern.user_id).order_by('-created_at')[:20]) \
        if intern.user_id else []
    return {
        'project': project, 'member': member, 'intern': intern, 'scores': scores,
        'average_score': average,
        'average_label': f"Активность: {str(average).replace('.', ',')}" if average is not None else '',
        'average_tone': 'success' if (average or 0) >= 8 else 'warning' if (average or 0) >= 5 else 'danger',
        'projects_count': intern.team_memberships.count(),
        'history': list(TeamMember.objects.filter(project=project, intern=intern).order_by('-joined_at', '-pk')),
        'in_team': member.status == TeamMember.Status.ACTIVE,
        'tasks': tasks,
        # Предупреждения этого человека по этому проекту (история — все циклы)
        'warnings': list(intern.warnings.filter(project=project).select_related('issued_by').order_by('-created_at')),
        'active_warnings': (active := intern.warnings.filter(status='active').count()),
        'warnings_subtitle': f'Активных сейчас: {active}. История всех циклов сохраняется.',
        'attendance_url': f"{reverse(detail, args=[project.pk])}?tab=attendance",
        'crumbs': [{'label': home[0], 'href': reverse(home[1])},
                   {'label': project.name, 'href': f"{reverse(detail, args=[project.pk])}?tab=team"},
                   {'label': intern.full_name, 'href': ''}],
    }


def meeting_sheet_context(project, meeting, members, scope, editable, tab) -> dict:
    """Табель собрания (2.0): общий для ПМ (просмотр) и тимлида (отметки, оценки)."""
    from apps.teams.selectors import group_by_role

    sections = [section for section in group_by_role(members) if section['members']]
    attended = sum(1 for m in members if m.mark and m.mark.status in ('present', 'late'))
    marked = sum(1 for m in members if m.mark)
    given = [m.score['score'] for m in members if m.score['score'] is not None]
    rate = round(attended / marked * 100) if marked else None
    average = round(sum(given) / len(given), 1) if given else None
    prefix = 'pm_portal' if scope == 'pm' else 'lead_portal'
    detail = reverse(f'{prefix}:project_detail', args=[project.pk])
    home = ('Мои проекты', reverse(f'{prefix}:dashboard'))
    return {
        'project': project, 'meeting': meeting, 'sections': sections, 'editable': editable,
        'marked': marked, 'attended': attended, 'total_people': len(members),
        'rate': rate, 'rate_label': f'Посещаемость: {rate}%' if rate is not None else '',
        'rate_tone': 'success' if (rate or 0) >= 80 else 'warning' if (rate or 0) >= 60 else 'danger',
        'scored': len(given), 'average_score': average,
        'average_label': f"Средний балл: {str(average).replace('.', ',')}" if average is not None else '',
        'average_tone': 'success' if (average or 0) >= 8 else 'warning' if (average or 0) >= 5 else 'danger',
        'host_label': f'ведёт {meeting.host.full_name}' if meeting.host_id else '',
        'period_start': meeting.period_start, 'period_days': meeting.period_days,
        'title': f'{meeting.get_kind_display()} · {meeting.date:%d.%m.%Y}',
        'tab': tab, **sheet_urls(project, meeting, scope),
        'crumbs': [{'label': home[0], 'href': home[1]}, {'label': project.name, 'href': f'{detail}?tab=attendance'},
                   {'label': f'{meeting.date:%d.%m.%Y}', 'href': ''}],
    }


def sheet_urls(project, meeting, scope) -> dict:
    if scope != 'lead':
        return {'mark_url': '', 'score_url': '', 'mark_all_url': ''}
    args = [project.pk, meeting.pk]
    return {'mark_url': reverse('lead_portal:meeting_mark_toggle', args=args),
            'score_url': reverse('lead_portal:meeting_score', args=args),
            'mark_all_url': reverse('lead_portal:meeting_mark_all', args=args)}

"""Карточка проекта 2.0 для трёх ролей (UI-03C): руководитель, ПМ, тимлид.

Один шаблон projects/card.html и одни разделы. Что можно делать и куда
вести ссылки, решает только этот модуль — шаблоны не проверяют роль,
а выводят готовые адреса (пустой адрес = действия нет). Доступ к самому
проекту уже проверен во view: руководитель — middleware, ПМ —
pm_project_or_404, тимлид — lead_project_or_404.
"""
from django.urls import reverse
from django.utils import timezone

from apps.projects import card
from apps.projects.models import ProjectStage, ProjectStageKey, ProjectStatus
from apps.teams.models import TeamMember, TeamRole

TABS = {
    'head': [('overview', 'Обзор'), ('stages', 'Этапы'), ('team', 'Команда'), ('tasks', 'Задачи'),
             ('documents', 'Документы'), ('report', 'Отчёты')],
    'pm': [('overview', 'Обзор'), ('stages', 'Этапы'), ('team', 'Команда'), ('tasks', 'Задачи'),
           ('documents', 'Документы'), ('report', 'Отчёты'), ('attendance', 'Табель'), ('client', 'Заказчик')],
    'lead': [('overview', 'Обзор'), ('team', 'Команда'), ('tasks', 'Задачи'), ('attendance', 'Табель'),
             ('documents', 'Документы')],
}
DETAIL = {'head': 'projects:detail', 'pm': 'pm_portal:project_detail', 'lead': 'lead_portal:project_detail'}
HOME = {'head': ('Проекты', 'projects:list'), 'pm': ('Мои проекты', 'pm_portal:dashboard'),
        'lead': ('Мои проекты', 'lead_portal:dashboard')}


def tab_href(project, scope, tab):
    return f'{reverse(DETAIL[scope], args=[project.pk])}?tab={tab}'


def base_context(request, project, scope, tab) -> dict:
    stage_label, stage_confirmed = card.stage_label(project)
    tabs = TABS[scope]
    label, home = HOME[scope]
    return {
        'project': project, 'tab': tab, 'scope': scope,
        'tabs': [{'key': k, 'label': l, 'href': f'?tab={k}'} for k, l in tabs],
        'pending_meeting': None,
        'status_tone': card.STATUS_TONES.get(project.status, 'neutral'),
        'stage_label': stage_label, 'stage_confirmed': stage_confirmed,
        'today': timezone.localdate(),
        'subtitle': ' · '.join(str(x) for x in (project.display_code, project.client) if x),
        'crumbs': [{'label': label, 'href': reverse(home)}, {'label': project.name, 'href': ''}],
        'is_head': scope == 'head',
    }


def valid_tab(scope, tab, extra=()):
    keys = [k for k, _ in TABS[scope]] + list(extra)
    return tab if tab in keys else 'overview'


def card_context(request, project, scope, tab) -> dict:
    context = base_context(request, project, scope, tab)
    builder = BUILDERS.get(tab)
    if builder:
        context.update(builder(request, project, scope))
    return context


# ------------------------------------------------------------------ разделы

def _overview(request, project, scope):
    from apps.projects.views import closure_context

    from apps.projects.graphics import stage_roadmap

    roadmap = stage_roadmap(project)
    data = {'overview': card.overview(project), 'roadmap': roadmap,
            'roadmap_label': f"Пройдено {roadmap['done']} из {roadmap['total']}"
                             + (f" · сейчас: {roadmap['current']['title']}" if roadmap['current'] else ''),
            'roadmap_percent_label': f"{roadmap['percent']}%"}
    data['last_report'] = data['overview']['last_report']
    data.update(closure_context(request, project))
    data['client_url'] = (project.client.get_absolute_url() if scope == 'head' else
                          tab_href(project, scope, 'client') if scope == 'pm' else '') if project.client_id else ''
    data['show_report'] = scope != 'lead'
    if scope == 'pm':
        data['edit_links'] = [
            {'label': 'Тип проекта', 'href': reverse('pm_portal:project_edit', args=[project.pk, 'type'])},
            {'label': 'Ссылки', 'href': reverse('pm_portal:project_edit', args=[project.pk, 'links'])},
        ]
    if scope == 'lead':
        from apps.lead_portal.overview import lead_overview
        from apps.lead_portal.services import lead_own_role

        lead = lead_overview(project, lead_own_role(request.user))
        for row in lead.get('attention', []):
            row['url'] = reverse('lead_portal:intern_detail', args=[project.pk, row['person'].pk])
        for meeting in list(lead.get('unmarked', [])) + list(lead.get('upcoming', [])):
            meeting.ui_url = reverse('lead_portal:meeting_detail', args=[project.pk, meeting.pk])
        data['lead'] = lead
    return data


def _stages(request, project, scope):
    data = {'stage_data': card.stage_rows(project), 'stage_statuses': ProjectStage.Status.choices,
            'can_edit_stages': scope in ('head', 'pm'),
            'can_set_primary_stage': scope in ('head', 'pm')}
    for stage in data['stage_data']['stages']:
        if scope == 'head':
            stage.ui_status_url = reverse('projects:stage_status_set', args=[stage.pk])
        elif scope == 'pm' and stage.key != ProjectStageKey.COMPLETED:
            stage.ui_status_url = reverse('pm_portal:stage_set', args=[project.pk, stage.pk])
        else:
            stage.ui_status_url = ''
    data['primary_stage_action'] = (
        reverse('projects:primary_stage_set', args=[project.pk]) if scope == 'head' else
        reverse('pm_portal:primary_stage_set', args=[project.pk]) if scope == 'pm' else '')
    if scope == 'pm':
        from apps.pm_portal import stages as reminders

        data['stage_check_needed'] = reminders.needs_stage_check(project)
        data['stages_confirm_url'] = reverse('pm_portal:stages_confirm', args=[project.pk])
    return data


def _team(request, project, scope):
    team = card.team(project, request.GET, include_left=request.GET.get('left') == '1')
    own_role = None
    if scope == 'lead':
        from apps.lead_portal.services import lead_own_role

        own_role = lead_own_role(request.user)
        # Тимлид видит ответственных и своё направление — чужие направления не его зона
        team['sections'] = [s for s in team['sections'] if s['role'] == own_role]
        team['directions'] = [d for d in team['directions'] if d[0] == own_role]
    for section in team['sections']:
        for member in section['members']:
            member.ui_url, member.ui_edit_url = _member_urls(project, scope, member, own_role)
    data = {'team': team, 'team_sections': team['sections'], 'can_recruit': scope == 'head'}
    data['team_add_url'] = (reverse('teams:member_add', args=[project.pk]) if scope == 'head' else
                            reverse('pm_portal:member_add', args=[project.pk]) if scope == 'pm' else '')
    if project.status != ProjectStatus.ACTIVE and scope != 'head':
        data['team_add_url'] = ''
    if scope == 'lead':
        from apps.interns.services import active_profile_form_link
        from apps.interns.views import PROFILE_LINK_TTL_CHOICES

        data.update({
            'profile_link': active_profile_form_link(project), 'profile_link_ttls': PROFILE_LINK_TTL_CHOICES,
            'profile_link_create': reverse('lead_portal:profile_link_create', args=[project.pk]),
            'profile_link_disable': reverse('lead_portal:profile_link_disable', args=[project.pk]),
            'is_active_lead': TeamMember.objects.filter(project=project, intern__user=request.user,
                                                        role=TeamRole.TEAM_LEAD, status=TeamMember.Status.ACTIVE).exists(),
        })
    return data


def _member_urls(project, scope, member, own_role):
    if not member.intern_id:
        return '', ''
    active = member.status == TeamMember.Status.ACTIVE
    if scope == 'head':
        return member.intern.get_absolute_url(), reverse('teams:member_edit', args=[member.pk]) if active else ''
    if scope == 'pm':
        if not active:
            return '', ''  # история бывших участников — у руководителя
        return (reverse('pm_portal:intern_detail', args=[project.pk, member.intern_id]),
                reverse('pm_portal:member_edit', args=[project.pk, member.pk]))
    if member.role == own_role:
        return (reverse('lead_portal:intern_detail', args=[project.pk, member.intern_id]),
                reverse('lead_portal:member_edit', args=[project.pk, member.pk]) if active else '')
    return '', ''


def _tasks(request, project, scope):
    from apps.tasks.project_views import tab_context

    return tab_context(request, project, scope)


def _documents(request, project, scope):
    from apps.documents.project_views import tab_context

    return tab_context(request, project, scope)


def _reports(request, project, scope):
    reports = list(project.reports.select_related('author').order_by('-date', '-created_at'))
    for report in reports:
        if scope == 'head':
            report.ui_update_url = reverse('projects:report_update', args=[report.pk])
            report.ui_delete_url = reverse('projects:report_delete', args=[report.pk])
        else:
            report.ui_update_url = reverse('pm_portal:report_update', args=[project.pk, report.pk])
            report.ui_delete_url = reverse('pm_portal:report_delete', args=[project.pk, report.pk])
        report.ui_title = f'Отчёт от {report.date:%d.%m.%Y}'
        report.ui_modal = f'report-edit-{report.pk}'
        report.ui_form = f'report-edit-form-{report.pk}'
    return {'reports': reports, 'report_create_url': reverse(
        'projects:report_create' if scope == 'head' else 'pm_portal:report_create', args=[project.pk])}


def _attendance(request, project, scope):
    group = getattr(project, 'group', None)
    data = {'group': group, 'meetings': [], 'attendance_note': (
        'Посещаемость и оценки ведёт тимлид — здесь только просмотр.' if scope == 'pm'
        else 'Только собрания вашего направления.')}
    if group is None:
        return data
    meetings = group.meetings.select_related('host').order_by('-date')
    if scope == 'lead':
        from apps.lead_portal.services import lead_own_role, pending_meeting

        own_role = lead_own_role(request.user)
        if own_role:
            meetings = meetings.filter(direction__in=['', own_role])
        data['pending_meeting'] = pending_meeting(project, request.user)
        if data['pending_meeting']:
            data['pending_title'] = f"Не закрыт табель за {data['pending_meeting'].date:%d.%m.%Y}"
            data['pending_meeting'].ui_url = reverse('lead_portal:meeting_detail', args=[project.pk, data['pending_meeting'].pk])
        data['meeting_create_url'] = reverse('lead_portal:meeting_create', args=[project.pk])
    detail = 'pm_portal:meeting_detail' if scope == 'pm' else 'lead_portal:meeting_detail'
    meetings = list(meetings)
    for meeting in meetings:
        meeting.ui_url = reverse(detail, args=[project.pk, meeting.pk])
    data['meetings'] = meetings
    return data


def _client(request, project, scope):
    return {'client': project.client, 'client_edit_url': reverse('pm_portal:client_edit', args=[project.pk])}


BUILDERS = {
    'overview': _overview, 'stages': _stages, 'team': _team, 'tasks': _tasks,
    'documents': _documents, 'report': _reports, 'attendance': _attendance, 'client': _client,
}

"""Отчёты и оценки в порталах тимлида и ПМ (этап 7B).

Портал пускает только свою роль (LeadScopeMiddleware / PMScopeMiddleware);
права на конкретный проект и стажёра проверяет apps.reports.weekly.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.interns.models import Intern
from apps.projects.models import Project
from apps.reports import weekly
from apps.reports.models import LeadWeeklyReport, PmWeeklyReport, WeeklyEvaluation
from apps.tasks.models import DIRECTION_CHOICES

DIRECTIONS = dict(DIRECTION_CHOICES)


def _flash(request, error):
    messages.error(request, ' '.join(getattr(error, 'messages', None) or [str(error)]))


def _weeks(today):
    return [{'key': w.start.isoformat(), 'label': ('Текущая: ' if i == 0 else 'Прошлая: ') + w.label}
            for i, w in enumerate(weekly.allowed_weeks(today))]


# ================================================================ тимлид

@login_required
def lead_reports(request):
    lead = getattr(request.user, 'intern_profile', None)
    if lead is None:
        raise PermissionDenied
    today = timezone.localdate()
    weeks = weekly.allowed_weeks(today)
    chosen = request.GET.get('week') or weeks[0].start.isoformat()
    week = next((w for w in weeks if w.start.isoformat() == chosen), weeks[0])
    obligations = weekly.lead_obligations(week, today, lead=lead)
    projects = []
    from apps.teams.staff import lead_member_direction

    seen = set()
    for member in weekly.lead_assignments(lead):
        project = member.project
        direction = lead_member_direction(member)
        if (project.pk, direction) in seen:
            continue
        seen.add((project.pk, direction))
        rows = [r for r in weekly.evaluable(lead, project) if r.role == direction]
        marks = {e.intern_id: e for e in WeeklyEvaluation.objects.filter(project=project, week_start=week.start)}
        for row in rows:
            row.ui_mark = marks.get(row.intern_id)
            row.ui_foreign = bool(row.ui_mark and row.ui_mark.lead_id != lead.pk)
            row.ui_form = f'eval-{project.pk}-{row.intern_id}'
        obligation = next((o for o in obligations if o.project.pk == project.pk and o.direction == direction), None)
        projects.append({'project': project, 'direction': direction,
                         'direction_label': DIRECTIONS.get(direction, 'направление не указано'),
                         'title': f'{project.name} · {DIRECTIONS.get(direction, "—")}',
                         'interns': rows, 'obligation': obligation,
                         'submit_url': reverse('lead_portal:weekly_report_submit', args=[project.pk]),
                         'evaluate_url': reverse('lead_portal:weekly_evaluate', args=[project.pk])})
    return render(request, 'reports/lead_reports.html', {
        'lead': lead, 'direction': DIRECTIONS.get(weekly.lead_direction(lead), 'направление не указано'),
        'week': week, 'weeks': _weeks(today), 'week_key': week.start.isoformat(), 'projects': projects,
        'scale': range(1, 11),
        'history': LeadWeeklyReport.objects.filter(lead=lead).select_related('project')[:50],
        'evaluations': WeeklyEvaluation.objects.filter(lead=lead).select_related('intern', 'project')[:100],
        'crumbs': [{'label': 'Мои проекты', 'href': reverse('lead_portal:dashboard')},
                   {'label': 'Отчёты и оценки', 'href': ''}],
    })


@login_required
@require_POST
def lead_report_submit(request, pk):
    project = get_object_or_404(Project, pk=pk)
    try:
        report = weekly.submit_lead_report(request.user, project, request.POST.get('text'),
                                           kind=request.POST.get('kind', LeadWeeklyReport.Kind.WEEKLY),
                                           week_start=request.POST.get('week'),
                                           direction=request.POST.get('direction') or None)
    except (weekly.ReportError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, f'Отчёт по «{project.name}» сохранён ({report.get_kind_display().lower()}).')
    return redirect(f"{reverse('lead_portal:weekly_reports')}?week={request.POST.get('week', '')}")


@login_required
@require_POST
def lead_evaluate(request, pk):
    project = get_object_or_404(Project, pk=pk)
    intern = get_object_or_404(Intern, pk=request.POST.get('intern') or 0)
    try:
        _mark, made = weekly.evaluate(request.user, intern, project, request.POST.get('score'),
                                      request.POST.get('comment', ''), week_start=request.POST.get('week'))
    except (weekly.ReportError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, f'{intern.full_name}: оценка {"сохранена" if made else "обновлена"}.')
    return redirect(f"{reverse('lead_portal:weekly_reports')}?week={request.POST.get('week', '')}#p{project.pk}")


# =================================================================== ПМ

@login_required
def pm_reports(request):
    pm = getattr(request.user, 'intern_profile', None)
    if pm is None:
        raise PermissionDenied
    today = timezone.localdate()
    own = list(weekly.pm_projects_now(pm))
    weeks = weekly.allowed_weeks(today)
    done = {r.week_start: r for r in PmWeeklyReport.objects.filter(pm=pm, week_start__in=[w.start for w in weeks])}
    from apps.pm_portal.services import pm_projects

    project_ids = list(pm_projects(request.user).values_list('pk', flat=True))
    lead_rows = []
    for week in weeks:
        for item in weekly.lead_obligations(week, today, project_ids=project_ids):
            item.ui_direction = DIRECTIONS.get(item.direction, 'направление не указано')
            lead_rows.append(item)
    return render(request, 'reports/pm_reports.html', {
        'pm': pm, 'own': own,
        'weeks': [{'key': w.start.isoformat(), 'label': f['label'], 'done': done.get(w.start)}
                  for w, f in zip(weeks, _weeks(today))],
        'can_submit': bool(own) and any(w.start not in done for w in weeks),
        'is_on_time_day': today.weekday() in weekly.ON_TIME_WEEKDAYS,
        'history': PmWeeklyReport.objects.filter(pm=pm).prefetch_related('projects')[:50],
        'lead_obligations': lead_rows,
        'lead_reports': LeadWeeklyReport.objects.filter(project_id__in=project_ids)
        .select_related('lead', 'project')[:100],
        'crumbs': [{'label': 'Мои проекты', 'href': reverse('pm_portal:dashboard')},
                   {'label': 'Отчёты', 'href': ''}],
    })


@login_required
@require_POST
def pm_report_submit(request):
    try:
        report = weekly.submit_pm_report(request.user, request.POST.get('text'),
                                         project_ids=request.POST.getlist('projects'),
                                         week_start=request.POST.get('week'))
    except (weekly.ReportError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, 'Сводный отчёт сохранён' + (' — в срок.' if report.on_time else
                                                             ' — не в срок (срок: пятница или суббота).'))
    return redirect('pm_portal:weekly_reports')

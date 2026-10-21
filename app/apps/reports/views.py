"""Еженедельные отчёты для руководителя (этап 7B): показатели сдачи,
отсутствующие обязательные отчёты, отчёты ПМ и тимлидов, оценки.
Только чтение; доступ — RoleAccessMiddleware (руководитель)."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from apps.dashboard import metrics
from apps.projects.models import Project
from apps.reports import weekly
from apps.reports.models import LeadWeeklyReport, PmWeeklyReport, WeeklyEvaluation
from apps.tasks.models import DIRECTION_CHOICES

DIRECTIONS = dict(DIRECTION_CHOICES)


@login_required
def board(request):
    from apps.accounts.access import has_full_access

    if not has_full_access(request.user):
        raise PermissionDenied
    today = timezone.localdate()
    week = metrics.parse_week(request.GET.get('week'), today)
    project_id = request.GET.get('project', '')
    direction = request.GET.get('direction', '')
    project_ids = [int(project_id)] if project_id.isdigit() else None
    direction = direction if direction in DIRECTIONS else ''
    obligations = weekly.lead_obligations(week, today, project_ids=project_ids, direction=direction)
    leads = LeadWeeklyReport.objects.filter(week_start=week.start).select_related('lead', 'project', 'author')
    evaluations = WeeklyEvaluation.objects.filter(week_start=week.start).select_related('intern', 'project', 'lead')
    pm = PmWeeklyReport.objects.filter(week_start=week.start).select_related('pm').prefetch_related('projects')
    if project_ids:
        leads, evaluations = leads.filter(project_id__in=project_ids), evaluations.filter(project_id__in=project_ids)
        pm = pm.filter(projects__in=project_ids).distinct()
    if direction:
        leads, evaluations = leads.filter(direction=direction), evaluations.filter(direction=direction)
    pm_summary = metrics.pm_reports(week, today)
    for item in obligations:
        item.ui_direction = DIRECTIONS.get(item.direction, 'направление не указано')
    leads = list(leads)
    for report in leads:
        report.ui_direction = DIRECTIONS.get(report.direction, '—')
    counts = {s: sum(1 for o in obligations if o.status == s) for s in ('submitted', 'pending', 'missed')}
    return render(request, 'reports/weekly_board.html', {
        'week': week, 'weeks': metrics.recent_weeks(8, today), 'params': request.GET,
        'projects': Project.objects.active().order_by('name'), 'directions': DIRECTION_CHOICES,
        'obligations': obligations, 'missing': [o for o in obligations if o.status == 'missed'],
        'counts': counts, 'lead_reports': leads, 'pm_reports': list(pm), 'pm_summary': pm_summary,
        'evaluations': list(evaluations),
        'labels': {
            'expected': str(len(obligations)), 'submitted': str(counts['submitted']),
            'missed': str(counts['missed']) if week.complete else 'неделя идёт',
            'pm_on_time': f'{pm_summary["on_time"]} из {pm_summary["expected"]}',
            'pm_late': str(pm_summary['other_day']),
            'pm_missing': str(pm_summary['missing']) if pm_summary['due'] else 'срок не наступил',
        },
        'crumbs': [{'label': 'Главная', 'href': reverse('dashboard')},
                   {'label': 'Еженедельные отчёты', 'href': ''}],
    })

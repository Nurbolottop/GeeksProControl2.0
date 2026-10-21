"""ReportingService (ТЗ §24, §25)."""
import datetime

from django.utils import timezone

from apps.interns.models import Intern, InternStatus, WORKING_STATUSES
from apps.projects import selectors as project_selectors
from apps.projects.models import Project, ProjectStageKey, ProjectStatus
from apps.reports.models import KPISnapshot, WeeklyReport
from apps.teams.models import TeamMember

# Человекочитаемые подписи показателей недельного отчёта
WEEKLY_LABELS = {
    'active_projects': 'Активные проекты',
    'started': 'Запущено за неделю',
    'completed': 'Завершено за неделю',
    'overdue': 'Просрочено',
    'at_risk': 'В риске',
    'on_delivery': 'На сдаче',
    'new_interns': 'Новые стажёры',
    'active_interns': 'Активные стажёры',
    'employable_interns': 'Готовы к трудоустройству',
    'overloaded_people': 'Перегруженные люди',
    'missing_documents': 'Проектов с неполными документами',
}


def week_bounds(date: datetime.date) -> tuple[datetime.date, datetime.date]:
    start = date - datetime.timedelta(days=date.weekday())
    return start, start + datetime.timedelta(days=6)


def weekly_metrics(week_start: datetime.date) -> dict:
    """Показатели за неделю (ТЗ §24.1)."""
    week_end = week_start + datetime.timedelta(days=6)
    active = project_selectors.active_projects()

    overloaded = 0
    totals: dict[tuple, int] = {}
    for member in TeamMember.objects.filter(status=TeamMember.Status.ACTIVE):
        key = ('u', member.user_id) if member.user_id else ('i', member.intern_id)
        totals[key] = totals.get(key, 0) + member.workload
    overloaded = sum(1 for total in totals.values() if total > 100)

    from apps.documents import services as doc_services
    missing_docs = sum(
        1 for project in active
        if doc_services.document_progress(project)['missing']
    )

    return {
        'active_projects': active.count(),
        'started': Project.objects.active().filter(
            start_date__gte=week_start, start_date__lte=week_end,
        ).count(),
        'completed': Project.objects.active().filter(
            status=ProjectStatus.COMPLETED,
            actual_end_date__gte=week_start, actual_end_date__lte=week_end,
        ).count(),
        'overdue': project_selectors.overdue_projects(week_end).count(),
        'at_risk': project_selectors.at_risk_projects(week_end).count()
                   + project_selectors.behind_projects(week_end).count(),
        'on_delivery': active.filter(
            current_stage=ProjectStageKey.DELIVERY,
        ).count(),
        'new_interns': Intern.objects.active().filter(
            internship_start_date__gte=week_start,
            internship_start_date__lte=week_end,
        ).count(),
        'active_interns': Intern.objects.active().filter(
            status__in=WORKING_STATUSES,
        ).count(),
        'employable_interns': Intern.objects.active().filter(
            status=InternStatus.EMPLOYABLE,
        ).count(),
        'overloaded_people': overloaded,
        'missing_documents': missing_docs,
    }


def generate_weekly_report(date: datetime.date | None = None) -> WeeklyReport:
    """Формирует отчёт за прошедшую неделю по форме руководителя."""
    from apps.reports import weekly_form

    from apps.dashboard.metrics import state_snapshot

    today = date or timezone.localdate()
    week_start, _ = week_bounds(today - datetime.timedelta(days=7))
    data = weekly_form.build(week_start)
    # Численность на момент снимка (понедельник утром ≈ конец прошлой
    # недели) — по ней Dashboard сравнивает недели. Снимаем только для
    # только что закончившейся недели: для давних недель сегодняшние
    # цифры были бы неправдой, там сравнение покажет «Нет данных».
    previous_week_start, _ = week_bounds(timezone.localdate() - datetime.timedelta(days=7))
    existing = WeeklyReport.objects.filter(week_start=week_start).first()
    if existing and 'dashboard_state' in (existing.data or {}):
        data['dashboard_state'] = existing.data['dashboard_state']
        data['dashboard_state_taken_at'] = existing.data.get('dashboard_state_taken_at')
    elif week_start == previous_week_start:
        data['dashboard_state'] = state_snapshot()
        data['dashboard_state_taken_at'] = timezone.now().isoformat()
    report, _created = WeeklyReport.objects.update_or_create(
        week_start=week_start, defaults={'data': data},
    )
    return report


def calculate_kpi() -> dict:
    """Текущие KPI руководителя (ТЗ §25)."""
    completed = Project.objects.active().filter(
        status=ProjectStatus.COMPLETED,
        planned_end_date__isnull=False, actual_end_date__isnull=False,
    )
    completed_count = completed.count()
    on_time = sum(
        1 for p in completed if p.actual_end_date <= p.planned_end_date
    )
    delays = [
        (p.actual_end_date - p.planned_end_date).days
        for p in completed
        if p.actual_end_date > p.planned_end_date
    ]
    durations = [
        p.duration_days for p in Project.objects.active().filter(
            status=ProjectStatus.COMPLETED,
        ) if p.duration_days
    ]

    from apps.documents import services as doc_services
    active = list(project_selectors.active_projects())
    doc_rates = []
    for project in active:
        progress = doc_services.document_progress(project)
        if progress['total']:
            doc_rates.append(progress['done'] / progress['total'])

    working_interns = Intern.objects.active().filter(
        status__in=WORKING_STATUSES,
    )
    busy_intern_ids = set(
        TeamMember.objects.filter(
            status=TeamMember.Status.ACTIVE, intern__isnull=False,
        ).values_list('intern_id', flat=True),
    )
    working_count = working_interns.count()
    busy_count = working_interns.filter(pk__in=busy_intern_ids).count()

    today = timezone.localdate()
    return {
        'on_time_delivery_rate': (
            round(on_time / completed_count * 100) if completed_count else None
        ),
        'completed_projects': completed_count,
        'average_delay_days': (
            round(sum(delays) / len(delays), 1) if delays else 0
        ),
        'average_duration_days': (
            round(sum(durations) / len(durations)) if durations else None
        ),
        'projects_at_risk': project_selectors.at_risk_projects(today).count()
                            + project_selectors.behind_projects(today).count(),
        'overdue_projects': project_selectors.overdue_projects(today).count(),
        'document_completeness': (
            round(sum(doc_rates) / len(doc_rates) * 100) if doc_rates else None
        ),
        'intern_utilization': (
            round(busy_count / working_count * 100) if working_count else None
        ),
    }


KPI_LABELS = {
    'on_time_delivery_rate': ('On-time Delivery Rate', '%'),
    'completed_projects': ('Завершённые проекты', ''),
    'average_delay_days': ('Средняя задержка', ' дн.'),
    'average_duration_days': ('Средняя длительность', ' дн.'),
    'projects_at_risk': ('Проекты в риске', ''),
    'overdue_projects': ('Просроченные проекты', ''),
    'document_completeness': ('Комплектность документов', '%'),
    'intern_utilization': ('Занятость стажёров', '%'),
}


def snapshot_kpi(period_type: str = KPISnapshot.Period.WEEK) -> KPISnapshot:
    """Сохраняет KPI snapshot за текущий период (ТЗ §25)."""
    today = timezone.localdate()
    if period_type == KPISnapshot.Period.WEEK:
        period_start, _ = week_bounds(today)
    else:
        period_start = today.replace(day=1)
    snapshot, _ = KPISnapshot.objects.update_or_create(
        period_type=period_type, period_start=period_start,
        defaults={'data': calculate_kpi()},
    )
    return snapshot


def earliest_project_date() -> datetime.date:
    """Дата первого проекта в системе — разумное значение «до» по умолчанию."""
    first = Project.objects.order_by('created_at').values_list(
        'created_at', flat=True,
    ).first()
    return first.date() if first else timezone.localdate()

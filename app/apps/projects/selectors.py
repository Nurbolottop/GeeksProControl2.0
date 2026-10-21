"""Выборки проектов для страниц и dashboard (ТЗ §46)."""
import datetime

from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.projects.models import DeadlineStatus, Project, ProjectStatus
from apps.projects.services import (
    HIGH_RISK_DAYS,
    HIGH_RISK_PROGRESS,
    INACTIVE_DAYS,
    RISK_DAYS,
    RISK_PROGRESS,
    calculate_deadline_status,
)


def projects_qs() -> QuerySet[Project]:
    return (
        Project.objects.active()
        .select_related('client', 'project_type', 'flow', 'primary_stage')
        .prefetch_related('team_members__intern')
    )


def active_projects() -> QuerySet[Project]:
    return projects_qs().filter(status=ProjectStatus.ACTIVE)


def projects_without_pm() -> list[Project]:
    """Активные проекты, где в команде нет ПМ."""
    return [p for p in active_projects() if not p.has_pm]


def projects_without_leads() -> list[Project]:
    """Активные проекты без тимлидов направлений."""
    return [p for p in active_projects() if not p.leads]


def overdue_projects(today: datetime.date | None = None) -> QuerySet[Project]:
    today = today or timezone.localdate()
    return active_projects().filter(planned_end_date__lt=today)


def at_risk_projects(today: datetime.date | None = None) -> QuerySet[Project]:
    """Риск задержки: deadline близко, прогресс недостаточный (ТЗ §22)."""
    today = today or timezone.localdate()
    return active_projects().filter(
        planned_end_date__gte=today,
        planned_end_date__lte=today + datetime.timedelta(days=RISK_DAYS),
        progress__lt=RISK_PROGRESS,
    ).exclude(
        planned_end_date__lte=today + datetime.timedelta(days=HIGH_RISK_DAYS),
        progress__lt=HIGH_RISK_PROGRESS,
    )


def behind_projects(today: datetime.date | None = None) -> QuerySet[Project]:
    """Отставание: deadline совсем близко, прогресс сильно недостаточный."""
    today = today or timezone.localdate()
    return active_projects().filter(
        planned_end_date__gte=today,
        planned_end_date__lte=today + datetime.timedelta(days=HIGH_RISK_DAYS),
        progress__lt=HIGH_RISK_PROGRESS,
    )


def inactive_projects() -> QuerySet[Project]:
    """Проекты без обновлений более N дней."""
    threshold = timezone.now() - datetime.timedelta(days=INACTIVE_DAYS)
    return active_projects().filter(
        Q(last_activity_at__lt=threshold) | Q(last_activity_at__isnull=True),
    )


def annotate_deadline_statuses(projects) -> list[Project]:
    """Проставляет каждому проекту вычисленный deadline_status."""
    today = timezone.localdate()
    result = []
    for project in projects:
        project.deadline_status = calculate_deadline_status(project, today)
        result.append(project)
    return result


def filter_projects(qs: QuerySet[Project], params) -> QuerySet[Project]:
    """Фильтры списка проектов (ТЗ §28)."""
    search = params.get('q', '').strip()
    if search:
        qs = qs.filter(
            Q(name__icontains=search)
            | Q(code__icontains=search)
            | Q(client__organization__icontains=search),
        )
    status = params.get('status')
    if status:
        qs = qs.filter(status=status)
    stage = params.get('stage')
    if stage:
        qs = qs.filter(current_stage=stage)
    # Основной этап (2.0): выбранный вручную или ещё не выбранный
    primary = params.get('primary_stage')
    if primary == 'unset':
        qs = qs.filter(primary_stage__isnull=True)
    elif primary:
        qs = qs.filter(primary_stage__key=primary)
    project_type = params.get('type')
    if project_type:
        qs = qs.filter(project_type_id=project_type)
    city = params.get('city')
    if city:
        qs = qs.filter(city=city)
    flow = params.get('flow')
    if flow:
        qs = qs.filter(flow=flow)
    today = timezone.localdate()
    deadline = params.get('deadline')
    if deadline == 'overdue':
        qs = qs.filter(planned_end_date__lt=today, status=ProjectStatus.ACTIVE)
    elif deadline == 'at_risk':
        qs = qs.filter(pk__in=at_risk_projects(today).values('pk'))
    elif deadline == 'behind':
        qs = qs.filter(pk__in=behind_projects(today).values('pk'))
    return qs

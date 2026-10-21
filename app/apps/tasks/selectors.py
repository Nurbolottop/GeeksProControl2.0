"""Выборки задач для страниц и dashboard."""
import datetime

from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.tasks.models import Task, TaskStatus

OPEN_STATUSES = (TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW)


def tasks_qs() -> QuerySet[Task]:
    return (
        Task.objects.active()
        .select_related('project', 'assignee', 'author')
    )


def open_tasks() -> QuerySet[Task]:
    return tasks_qs().filter(status__in=OPEN_STATUSES)


def overdue_tasks(today: datetime.date | None = None) -> QuerySet[Task]:
    today = today or timezone.localdate()
    return open_tasks().filter(deadline__lt=today)


def filter_tasks(qs: QuerySet[Task], params) -> QuerySet[Task]:
    """Фильтры списка задач (ТЗ §28): проект, исполнитель, статус, приоритет."""
    search = params.get('q', '').strip()
    if search:
        qs = qs.filter(
            Q(title__icontains=search) | Q(project__name__icontains=search),
        )
    project = params.get('project')
    if project:
        qs = qs.filter(project_id=project)
    assignee = params.get('assignee')
    if assignee:
        qs = qs.filter(assignee_id=assignee)
    status = params.get('status')
    if status:
        qs = qs.filter(status=status)
    priority = params.get('priority')
    if priority:
        qs = qs.filter(priority=priority)

    today = timezone.localdate()
    view = params.get('view')
    if view == 'overdue':
        qs = qs.filter(deadline__lt=today, status__in=OPEN_STATUSES)
    elif view == 'today':
        qs = qs.filter(deadline=today, status__in=OPEN_STATUSES)
    elif view == 'week':
        week_end = today + datetime.timedelta(days=7)
        qs = qs.filter(
            deadline__gte=today, deadline__lte=week_end,
            status__in=OPEN_STATUSES,
        )
    return qs

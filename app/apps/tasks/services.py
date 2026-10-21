"""Бизнес-логика задач: автоматические чек-листы (ТЗ §10.1)."""
from django.db import transaction
from django.utils import timezone

from apps.projects.models import Project
from apps.tasks.models import Task, TaskStatus, TaskTemplate

# Значения по умолчанию — используются, пока в БД нет настроенных шаблонов.
DEFAULT_TEMPLATES = {
    TaskTemplate.Kind.PROJECT_NEW: [
        'Получить данные заказчика',
        'Подготовить договор',
        'Получить подпись',
        'Создать ТЗ',
        'Назначить PM',
        'Назначить TL',
        'Сформировать команду',
        'Провести kickoff',
        'Создать repository',
    ],
    TaskTemplate.Kind.DELIVERY: [
        'Проверить готовность функционала',
        'Развернуть production',
        'Подключить домен и SSL',
        'Настроить backup',
        'Завершить QA, закрыть critical bugs',
        'Подготовить акт',
        'Провести demo клиенту',
        'Передать доступы',
        'Получить подпись акта',
    ],
}


def _template_titles(kind: str) -> list[str]:
    titles = list(
        TaskTemplate.objects.filter(kind=kind, is_active=True)
        .order_by('order').values_list('title', flat=True),
    )
    return titles or DEFAULT_TEMPLATES[kind]


@transaction.atomic
def generate_checklist(project: Project, kind: str, user=None) -> list[Task]:
    """Создаёт типовой чек-лист задач для проекта.

    Повторный вызов не дублирует уже существующие задачи с теми же названиями.
    """
    existing = set(
        project.tasks.filter(is_archived=False).values_list('title', flat=True),
    )
    tasks = [
        Task(project=project, title=title, author=user)
        for title in _template_titles(kind)
        if title not in existing
    ]
    Task.objects.bulk_create(tasks)
    return tasks


def set_task_status(task: Task, status: str, user=None) -> Task:
    """Смена статуса задачи с автоматикой даты завершения."""
    task.status = status
    if status == TaskStatus.DONE and not task.completed_at:
        task.completed_at = timezone.localdate()
    elif status != TaskStatus.DONE:
        task.completed_at = None
    task.save(update_fields=['status', 'completed_at', 'updated_at'])
    if task.project_id:
        task.project.last_activity_at = timezone.now()
        task.project.save(update_fields=['last_activity_at', 'updated_at'])
    return task

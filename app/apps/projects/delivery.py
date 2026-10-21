"""Сдача проекта.

В 2.0 проект закрывает только руководитель через apps.projects.closure:
принудительного закрытия в обход условий больше нет. Здесь остались
совместимые обёртки для старых мест, которые показывают проверки.
"""
from apps.projects.models import Project


def delivery_checks(project: Project) -> list[dict]:
    """Обязательные условия закрытия: [{group, label, ok, hint}]."""
    from apps.projects.closure import readiness

    return [
        {'group': 'Документы' if item['kind'] == 'document' else 'Подтверждения руководителя',
         'label': item['label'], 'ok': item['ok'], 'hint': item['hint']}
        for item in readiness(project).items
    ]


def failed_checks(project: Project) -> list[dict]:
    return [c for c in delivery_checks(project) if not c['ok']]


def delay_days_on_completion(project: Project) -> int | None:
    """Задержка сдачи относительно плановой даты (для KPI)."""
    if project.planned_end_date and project.actual_end_date:
        return (project.actual_end_date - project.planned_end_date).days
    return None

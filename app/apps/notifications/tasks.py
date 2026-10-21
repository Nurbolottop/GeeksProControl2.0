"""Автоматические проверки (ТЗ §22, §37). Запускаются Celery Beat ежедневно."""
from celery import shared_task
from django.utils import timezone

from apps.notifications.models import NotificationLevel
from apps.notifications.services import notify


@shared_task
def run_daily_checks() -> int:
    """Все ежедневные проверки; возвращает число созданных уведомлений."""
    created = 0
    created += check_deadlines()
    created += check_inactive_projects()
    created += check_missing_documents()
    created += check_resource_overload()
    created += check_missing_managers()
    from apps.reports.weekly import remind

    created += remind()  # еженедельные отчёты: только внутренние уведомления
    from apps.video.checks import run as check_meetings

    created += check_meetings()  # обязательные собрания (этап 9B)
    ensure_weekly_snapshot()
    return created


def ensure_weekly_snapshot() -> bool:
    """Этап 10A: если понедельничный запуск Beat пропущен (контейнер не работал),
    снимок прошлой недели досоздаётся. Время снятия честно пишется в
    dashboard_state_taken_at — задним числом значения не подделываются."""
    import datetime

    from apps.reports.models import WeeklyReport
    from apps.reports.services import generate_weekly_report

    today = timezone.localdate()
    week_start = today - datetime.timedelta(days=today.weekday() + 7)
    if WeeklyReport.objects.filter(week_start=week_start).exists():
        return False
    generate_weekly_report()
    return True


def check_deadlines() -> int:
    """Риск / отставание / просрочка по deadline (ТЗ §22)."""
    from apps.projects import selectors
    today = timezone.localdate()
    created = 0
    for project in selectors.overdue_projects(today):
        days = (today - project.planned_end_date).days
        if notify(
            f'Проект просрочен: {project.name}',
            description=f'Deadline {project.planned_end_date:%d.%m.%Y}, просрочка {days} дн.',
            level=NotificationLevel.CRITICAL,
            url=project.get_absolute_url(),
            dedup_key=f'overdue:{project.pk}',
        ):
            created += 1
    for project in selectors.behind_projects(today):
        if notify(
            f'Отставание: {project.name}',
            description=f'Прогресс {project.progress}% при deadline {project.planned_end_date:%d.%m.%Y}',
            level=NotificationLevel.WARNING,
            url=project.get_absolute_url(),
            dedup_key=f'behind:{project.pk}',
        ):
            created += 1
    for project in selectors.at_risk_projects(today):
        if notify(
            f'Риск задержки: {project.name}',
            description=f'Прогресс {project.progress}% при deadline {project.planned_end_date:%d.%m.%Y}',
            level=NotificationLevel.WARNING,
            url=project.get_absolute_url(),
            dedup_key=f'at_risk:{project.pk}',
        ):
            created += 1
    return created


def check_inactive_projects() -> int:
    from apps.projects import selectors
    created = 0
    for project in selectors.inactive_projects():
        if notify(
            f'Нет обновлений: {project.name}',
            description='Проект без активности более 3 дней.',
            level=NotificationLevel.WARNING,
            url=project.get_absolute_url(),
            dedup_key=f'inactive:{project.pk}',
        ):
            created += 1
    return created


def check_missing_documents() -> int:
    """Delivery без обязательных документов (ТЗ §22)."""
    from apps.documents import services as doc_services
    from apps.projects import selectors
    from apps.projects.models import ProjectStageKey
    created = 0
    for project in selectors.active_projects().filter(
        current_stage=ProjectStageKey.DELIVERY,
    ):
        progress = doc_services.document_progress(project)
        if progress['missing']:
            names = ', '.join(t.name for t in progress['missing'])
            if notify(
                f'Сдача без документов: {project.name}',
                description=f'Отсутствуют: {names}',
                level=NotificationLevel.CRITICAL,
                url=f'{project.get_absolute_url()}?tab=documents',
                dedup_key=f'missing_docs:{project.pk}',
            ):
                created += 1
    return created


def check_resource_overload() -> int:
    """Загрузка человека > 100% (ТЗ §22)."""
    from django.urls import reverse

    from apps.teams.models import TeamMember
    totals: dict[tuple, dict] = {}
    for member in TeamMember.objects.filter(
        status=TeamMember.Status.ACTIVE,
    ).select_related('user', 'intern'):
        key = ('u', member.user_id) if member.user_id else ('i', member.intern_id)
        entry = totals.setdefault(key, {'name': member.person_name, 'total': 0})
        entry['total'] += member.workload
    created = 0
    for key, entry in totals.items():
        if entry['total'] > 100:
            if notify(
                f'Перегруз: {entry["name"]} ({entry["total"]}%)',
                level=NotificationLevel.WARNING,
                url=reverse('teams:overview'),
                dedup_key=f'overload:{key[0]}:{key[1]}',
            ):
                created += 1
    return created


def check_missing_managers() -> int:
    """Активные проекты без PM или Team Lead (ТЗ §22)."""
    from apps.projects import selectors
    created = 0
    for project in selectors.projects_without_pm():
        if notify(
            f'Нет ПМ: {project.name}',
            level=NotificationLevel.WARNING,
            url=project.get_absolute_url(),
            dedup_key=f'no_pm:{project.pk}',
        ):
            created += 1
    for project in selectors.projects_without_leads():
        if notify(
            f'Нет тимлидов: {project.name}',
            level=NotificationLevel.WARNING,
            url=project.get_absolute_url(),
            dedup_key=f'no_tl:{project.pk}',
        ):
            created += 1
    return created

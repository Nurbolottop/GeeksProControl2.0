"""Закрытие проекта (UI-03B) — единственный путь к статусу «Завершён».

Закрывает только руководитель. Обязательные условия (и только они):

* разработка завершена, проект опубликован в production, проект принят
  заказчиком — три фиксации руководителя (дата, автор, история);
* четыре обязательных документа: бриф заказчика, договор, ТЗ, подписанный
  акт приёма-передачи.

Домен, GitHub, доступы, открытые задачи, критичные баги, прогресс и срок —
справочные: показываются, но закрытие не блокируют.

close_project атомарна: строка проекта блокируется, условия проверяются
заново внутри транзакции, повторное закрытие отклоняется. Telegram — только
после commit (apps.projects.services.release_team).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.projects.models import Project, ProjectStageKey, ProjectStatus, ProjectStatusHistory

# Три фиксации руководителя: ключ → (поле даты, поле автора, подпись)
CONFIRMATIONS = {
    'dev': ('dev_completed_at', 'dev_completed_by', 'Разработка завершена'),
    'production': ('production_confirmed_at', 'production_confirmed_by', 'Проект опубликован в production'),
    'accepted': ('client_accepted_at', 'client_accepted_by', 'Проект принят заказчиком'),
}
CLOSABLE_STATUSES = (ProjectStatus.ACTIVE, ProjectStatus.PAUSED)
GRADUATES_FIELD = 'Выпускники'


class ClosureError(ValidationError):
    """Проект не закрыт: причины — в .messages."""


@dataclass
class Readiness:
    items: list[dict] = field(default_factory=list)
    info: list[dict] = field(default_factory=list)

    @property
    def missing(self) -> list[dict]:
        return [item for item in self.items if not item['ok']]

    @property
    def ready(self) -> bool:
        return not self.missing

    @property
    def done(self) -> int:
        return len(self.items) - len(self.missing)


def can_close(user) -> bool:
    from apps.accounts.access import has_full_access

    return has_full_access(user)


def _required_documents(project: Project) -> list[dict]:
    from apps.documents.project_documents import REQUIRED_DOCUMENTS, current

    items = []
    for code, label in REQUIRED_DOCUMENTS:
        document = current(project, code)
        items.append({
            'key': f'doc:{code}', 'kind': 'document', 'label': label, 'ok': document is not None,
            'at': document.created_at if document else None,
            'by': document.uploaded_by if document else None,
            'hint': 'Загрузите во вкладке «Документы»',
        })
    return items


def readiness(project: Project) -> Readiness:
    result = Readiness()
    for key, (at_field, by_field, label) in CONFIRMATIONS.items():
        at = getattr(project, at_field)
        result.items.append({
            'key': key, 'kind': 'confirm', 'label': label, 'ok': at is not None,
            'at': at, 'by': getattr(project, by_field), 'hint': 'Подтверждает руководитель',
        })
    result.items.extend(_required_documents(project))
    result.info = informational(project)
    return result


def informational(project: Project) -> list[dict]:
    """Справочно — закрытие не блокирует."""
    from apps.tasks.models import TaskPriority, TaskStatus

    open_statuses = (TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW)
    tasks = project.tasks.active()
    open_tasks = tasks.filter(status__in=open_statuses).count()
    critical = tasks.filter(status__in=open_statuses, priority=TaskPriority.CRITICAL).count()
    return [
        {'label': 'Домен', 'ok': bool(project.domain), 'value': project.domain or 'не указан'},
        {'label': 'GitHub', 'ok': bool(project.github_url or project.frontend_repo_url),
         'value': 'указан' if (project.github_url or project.frontend_repo_url) else 'не указан'},
        {'label': 'Доступы', 'ok': project.accesses.exists(),
         'value': 'загружены' if project.accesses.exists() else 'не загружены'},
        {'label': 'Открытые задачи', 'ok': open_tasks == 0, 'value': str(open_tasks)},
        {'label': 'Критичные баги', 'ok': critical == 0, 'value': str(critical)},
        {'label': 'Прогресс', 'ok': project.progress >= 100, 'value': f'{project.progress}%'},
    ]


def _history(project, field_label, old, new, user, reason=''):
    from apps.audit.services import log as audit_log

    ProjectStatusHistory.objects.create(
        project=project, field=field_label, old_value=old, new_value=new, reason=reason, user=user,
    )
    audit_log(project, f'Изменение: {field_label}', old_value=old, new_value=new, reason=reason, user=user)


@transaction.atomic
def set_confirmation(project: Project, key: str, value: bool, user) -> Project:
    """Руководитель фиксирует (или снимает) одно из трёх условий закрытия."""
    if not can_close(user):
        raise PermissionDenied('Условия закрытия фиксирует руководитель.')
    if key not in CONFIRMATIONS:
        raise ValidationError('Неизвестное условие.')
    locked = Project.objects.select_for_update().get(pk=project.pk)
    if locked.status == ProjectStatus.COMPLETED:
        raise ValidationError('Проект уже закрыт — условия закрытия не меняются.')
    at_field, by_field, label = CONFIRMATIONS[key]
    if (getattr(locked, at_field) is not None) == value:
        return locked
    now = timezone.now()
    setattr(locked, at_field, now if value else None)
    setattr(locked, by_field, user if value else None)
    locked.save(update_fields=[at_field, by_field, 'updated_at'])
    _history(locked, label, 'нет' if value else 'да',
             f'да ({timezone.localtime(now):%d.%m.%Y})' if value else 'нет (подтверждение снято)', user)
    return locked


@dataclass
class ClosureResult:
    project: Project
    graduates: list = field(default_factory=list)


def close_project(project: Project, user) -> ClosureResult:
    """Закрыть проект. ClosureError — с причинами; PermissionDenied — не руководитель."""
    if not can_close(user):
        raise PermissionDenied('Проект закрывает только руководитель.')
    from apps.interns.models import Intern
    from apps.notifications.models import NotificationLevel
    from apps.notifications.services import notify
    from apps.projects.services import release_team

    with transaction.atomic():
        locked = Project.objects.select_for_update().get(pk=project.pk)
        if locked.status == ProjectStatus.COMPLETED:
            raise ClosureError('Проект уже закрыт.')
        if locked.is_archived or locked.status not in CLOSABLE_STATUSES:
            raise ClosureError(f'Проект в статусе «{locked.get_status_display()}» не закрывается.')
        state = readiness(locked)  # заново, внутри транзакции
        if not state.ready:
            raise ClosureError([f'Не выполнено: {item["label"]}' for item in state.missing])

        today = timezone.localdate()
        old_status = locked.get_status_display()
        locked.status = ProjectStatus.COMPLETED
        locked.current_stage = ProjectStageKey.COMPLETED
        locked.actual_end_date = today
        locked.recruitment_open = False  # после закрытия новых участников нет
        locked.closed_by = user
        locked.last_activity_at = timezone.now()
        locked.save(update_fields=[
            'status', 'current_stage', 'actual_end_date', 'recruitment_open', 'closed_by',
            'last_activity_at', 'updated_at',
        ])
        graduate_ids = release_team(locked, today)
        _history(locked, 'Статус', old_status, ProjectStatus.COMPLETED.label, user,
                 reason=f'Проект закрыт руководителем {today:%d.%m.%Y}')
        graduates = list(Intern.objects.filter(pk__in=graduate_ids).order_by('full_name'))
        names = ', '.join(g.full_name for g in graduates)
        # Итог фиксируем сразу: позже «кто выпустился именно здесь» по
        # производным данным уже не восстановить однозначно
        ProjectStatusHistory.objects.create(
            project=locked, field=GRADUATES_FIELD, new_value='\n'.join(g.full_name for g in graduates) or 'нет', user=user,
        )
        from apps.audit.services import log as audit_log

        for person in graduates:
            audit_log(person, 'Выпуск по закрытию проекта', new_value=locked.name, user=user)
        notify(
            f'Проект «{locked.name}» закрыт', level=NotificationLevel.SUCCESS,
            description=f'Новых выпускников: {len(graduates)}' + (f' — {names}' if names else ''),
            url=locked.get_absolute_url(), dedup_key=f'project-completed:{locked.pk}',
        )
    project.refresh_from_db()
    return ClosureResult(project=project, graduates=graduates)

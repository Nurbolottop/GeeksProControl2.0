"""Этапы проекта в портале ПМ и красное напоминание их обновлять.

Напоминание горит каждый день, пока ПМ не обновит этап или не
подтвердит, что текущий этап актуален. На следующий день загорается
снова — этапы должны сверяться постоянно, а не «когда вспомнят».
"""
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.projects import services as project_services
from apps.projects.models import (
    ProjectStage, ProjectStageKey, ProjectStatus, ProjectStatusHistory,
)

# От чьего имени просьба — руководитель, который следит за этапами
# Чьим именем подписано напоминание ПМ (имя руководителя) — из окружения,
# в коде реальных имён нет.
REMINDER_FROM = getattr(settings, 'STAGE_REMINDER_FROM', 'Руководитель')


def needs_stage_check(project, today=None) -> bool:
    """Горит ли у проекта напоминание: активный проект, сегодня не сверяли."""
    if project.status != ProjectStatus.ACTIVE:
        return False
    if project.stages_checked_at is None:
        return True
    today = today or timezone.localdate()
    return timezone.localtime(project.stages_checked_at).date() < today


def mark_checked(project) -> None:
    project.stages_checked_at = timezone.now()
    project.save(update_fields=['stages_checked_at', 'updated_at'])


def editable_stages(project) -> list[ProjectStage]:
    """Этапы, которые ПМ двигает сам. «Завершён» — это завершение проекта,
    его делает руководитель кнопкой «Завершить проект», не ПМ."""
    return list(
        project.stages.exclude(key=ProjectStageKey.COMPLETED).order_by('order'),
    )


@transaction.atomic
def set_stage_status(project, stage: ProjectStage, status: str, user=None) -> bool:
    """Сменить статус этапа от имени ПМ. Возвращает, было ли изменение.

    Вся автоматика (даты начала/завершения, текущий этап, процент) — та же,
    что в основном приложении; в историю проекта пишем, кто и что поменял.
    """
    changed = stage.status != status
    if changed:
        old = stage.get_status_display()
        stage.status = status
        project_services.update_stage(stage, user=user)
        ProjectStatusHistory.objects.create(
            project=project, field=f'Этап «{stage.get_key_display()}»',
            old_value=old, new_value=stage.get_status_display(),
            reason='Обновлено ПМ в портале', user=user,
        )
    project.refresh_from_db()
    mark_checked(project)
    return changed


def stage_alerts(projects) -> list:
    """Проекты ПМ, по которым сегодня ещё не сверили этапы."""
    today = timezone.localdate()
    return [project for project in projects if needs_stage_check(project, today)]

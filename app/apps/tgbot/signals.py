"""Новые и закрытые участия (save) → подключение к группе и уведомления (8B).
Выходы через .update() сверяет apps.tasks.workflow.release_stale."""
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.teams.models import TeamMember, TeamRole


@receiver(post_save, sender=TeamMember)
def on_team_member_saved(sender, instance, created, **kwargs):
    if not instance.intern_id or not instance.project_id or instance.role == TeamRole.TEAM_LEAD:
        return

    def after():
        from apps.tgbot import groups, notify

        try:
            groups.reconcile(project_ids=[instance.project_id], intern_ids=[instance.intern_id])
            if created and instance.status == TeamMember.Status.ACTIVE and instance.role != TeamRole.PROJECT_MANAGER:
                notify.project_assigned(instance.intern, instance.project, instance.get_role_display())
        except Exception:  # Telegram-учёт не должен ломать сохранение участия
            import logging

            logging.getLogger(__name__).exception('Сверка Telegram-групп не выполнена (member=%s)', instance.pk)

    transaction.on_commit(after)

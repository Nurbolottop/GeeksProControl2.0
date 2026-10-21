"""Автоматика резерва кадров.

Тимлидом человека делают из разных мест (список тимлидов, команда
проекта, карточка человека, портал тимлида), поэтому следим за самим
сохранением участника команды, а не за каждой из этих страниц. По той же
причине общую Google-таблицу обновляем по сохранению кандидата: неважно,
кто и откуда его добавил или поправил.
"""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.reserve.models import ReserveCandidate
from apps.teams.models import TeamMember, TeamRole


@receiver(post_save, sender=TeamMember, dispatch_uid='reserve_lead_in_reserve')
def lead_goes_to_reserve(sender, instance, raw=False, **kwargs):
    if raw or instance.role != TeamRole.TEAM_LEAD or not instance.intern_id:
        return
    from apps.reserve.services import ensure_lead_in_reserve

    ensure_lead_in_reserve(instance.intern)


@receiver(post_save, sender=ReserveCandidate, dispatch_uid='reserve_sheet_save')
def candidate_saved(sender, instance, raw=False, **kwargs):
    if raw:
        return
    from apps.reserve import gsheets

    gsheets.sync_later(f'кандидат сохранён: {instance.full_name}')


@receiver(post_delete, sender=ReserveCandidate, dispatch_uid='reserve_sheet_delete')
def candidate_deleted(sender, instance, **kwargs):
    from apps.reserve import gsheets

    gsheets.sync_later(f'кандидат удалён: {instance.full_name}')

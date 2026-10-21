"""Фоновые задачи резерва кадров."""
from celery import shared_task


@shared_task
def sync_reserve_sheet(reason: str = '') -> bool:
    """Переписать общую Google-таблицу резерва кадров."""
    from apps.reserve import gsheets

    return gsheets.sync(reason or 'celery')

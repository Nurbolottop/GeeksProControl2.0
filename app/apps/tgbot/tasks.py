from celery import shared_task


@shared_task
def flush_outbox() -> int:
    """Отправка очереди исходящих (через GuardedTeleBot) и повторы."""
    from apps.tgbot.outbox import flush

    return flush()

"""Напоминания по выпускникам — ежедневно, из Celery Beat.

Выпускник, который не сделал выбор, и человек, который давно ждёт
проект, иначе просто висят: ни им, ни руководителю об этом никто не
напоминает, и «Выпускники» копятся.
"""
import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# Сколько дней ждём, прежде чем напомнить, и как часто повторяем.
CHOICE_SILENCE_DAYS = 7
WAITING_SILENCE_DAYS = 14
REMINDER_EVERY_DAYS = 7


def _days_since(moment) -> int:
    if moment is None:
        return 10 ** 6
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    return (timezone.now() - moment).days


@shared_task
def remind_graduates() -> dict:
    """Напомнить выпускникам о выборе, а ждущим — что они в очереди."""
    from apps.interns.models import GraduateStatus
    from apps.interns.services import graduated_interns

    today = timezone.localdate()
    pending, waiting, sent = [], [], 0
    for person in graduated_interns():
        graduated_at = getattr(person, 'graduated_at', None)
        waited = (today - graduated_at).days if graduated_at else 10 ** 6
        if person.graduate_status == GraduateStatus.PENDING and waited >= CHOICE_SILENCE_DAYS:
            pending.append(person)
            sent += _nudge(person, (
                f'{person.full_name}, напоминаем: после сдачи проекта нужно '
                'выбрать, что дальше — продолжить стажировку или закончить '
                'её и попасть в банк резюме. Нажмите /start.'
            ))
        elif person.graduate_status == GraduateStatus.WAITING and waited >= WAITING_SILENCE_DAYS:
            waiting.append(person)
            sent += _nudge(person, (
                f'{person.full_name}, вы всё ещё в базе ожидания проекта. '
                'Как только появится место, мы напишем сюда — ждать ничего '
                'не нужно. Если планы изменились, скажите руководителю GeeksPro.'
            ))

    _tell_the_head(pending, waiting)
    return {'pending': len(pending), 'waiting': len(waiting), 'sent': sent}


def _nudge(person, text: str) -> int:
    """Написать человеку, если он знаком боту и его давно не трогали."""
    if not person.telegram_chat_id:
        return 0
    if _days_since(person.bot_reminded_at) < REMINDER_EVERY_DAYS:
        return 0
    try:
        from apps.graduate_bot.bot import bot

        bot.send_message(person.telegram_chat_id, text)
    except Exception:
        logger.exception('Не удалось напомнить выпускнику (intern=%s)', person.pk)
        return 0
    person.bot_reminded_at = timezone.now()
    person.save(update_fields=['bot_reminded_at', 'updated_at'])
    return 1


def _tell_the_head(pending, waiting) -> None:
    """Сводка руководителю: кто завис и сколько уже ждёт."""
    from django.urls import reverse

    from apps.notifications.models import NotificationLevel
    from apps.notifications.services import notify

    today = timezone.localdate()
    if pending:
        names = ', '.join(p.full_name for p in pending[:5])
        notify(
            f'Выпускники без решения: {len(pending)}',
            level=NotificationLevel.WARNING,
            description=(
                f'Дольше {CHOICE_SILENCE_DAYS} дней не выбрали, что дальше: {names}'
                + ('…' if len(pending) > 5 else '')
            ),
            url=reverse('interns:graduates'),
            dedup_key=f'graduates-stuck:{today}',
        )
    if waiting:
        names = ', '.join(p.full_name for p in waiting[:5])
        notify(
            f'Долго ждут проект: {len(waiting)}',
            level=NotificationLevel.WARNING,
            description=(
                f'Дольше {WAITING_SILENCE_DAYS} дней в базе ожидания: {names}'
                + ('…' if len(waiting) > 5 else '')
            ),
            url=reverse('interns:waiting'),
            dedup_key=f'graduates-waiting-long:{today}',
        )

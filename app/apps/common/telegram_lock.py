"""Один polling на токен (этап 10A): блокировка на уровне БД.

Telegram отдаёт обновления только одному getUpdates; два процесса с одним
токеном отбирают друг у друга сообщения. Любая команда, которая слушает
Telegram, берёт pg_try_advisory_lock на время жизни процесса; второй
процесс получает отказ и не стартует. Ключ один для всех команд бота.
"""
from django.db import connection

POLLING_LOCK_KEY = 8_800_100_001


class PollingLocked(Exception):
    pass


def acquire_polling_lock() -> None:
    """Держится до конца процесса (сессионная блокировка соединения)."""
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [POLLING_LOCK_KEY])
        if not cursor.fetchone()[0]:
            raise PollingLocked('Telegram уже слушает другой процесс (polling занят).')


def release_polling_lock() -> None:
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_unlock(%s)', [POLLING_LOCK_KEY])

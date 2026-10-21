"""Основной бот 2.0 (этап 8, 10A): long polling → apps.tgbot.dispatcher.handle_update.

Запуск — только отдельным решением владельца проекта. Все запросы идут через
telegram_guard: при TELEGRAM_OUTBOUND_MODE=off даже getUpdates заблокирован.
Вне production входящие обрабатываются только из allowlist (apps.tgbot.gate).
Один polling на токен — блокировка в БД (apps.common.telegram_lock): второй
процесс (в том числе старый run_graduate_bot) не стартует.
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

RETRY_START_SECONDS = 10
RETRY_MAX_SECONDS = 120


def _hide_token(message: str) -> str:
    token = settings.TELEGRAM_BOT_TOKEN
    if token and token in message:
        message = message.replace(token, '***')
    secret = token.split(':', 1)[-1] if token else ''
    return message.replace(secret, '***') if secret else message


class Command(BaseCommand):
    help = 'Telegram-бот GeeksPro 2.0 (long polling через защиту telegram_guard).'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Один цикл getUpdates и выход.')

    def handle(self, *args, **options):
        from apps.common import telegram_guard
        from apps.common.safety import is_protected
        from apps.common.telegram_lock import PollingLocked, acquire_polling_lock

        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError('TELEGRAM_BOT_TOKEN не задан.')
        if is_protected(settings.TELEGRAM_BOT_TOKEN):
            raise CommandError('Токен бота 1.0 в 2.0 запрещён.')
        if settings.TELEGRAM_OUTBOUND_MODE == 'off':
            raise CommandError('TELEGRAM_OUTBOUND_MODE=off — Telegram недоступен; включите allowlist для stage.')
        try:
            acquire_polling_lock()
        except PollingLocked as error:
            raise CommandError(str(error)) from None
        telegram_guard.install()
        from apps.graduate_bot.bot import bot
        from apps.tgbot.dispatcher import handle_update

        offset, delay = None, RETRY_START_SECONDS
        self.stdout.write('Бот 2.0 запущен (режим %s, окружение %s).' % (
            settings.TELEGRAM_OUTBOUND_MODE, settings.GP2_ENVIRONMENT))
        while True:
            try:
                updates = bot.get_updates(offset=offset, timeout=10, long_polling_timeout=10,
                                          allowed_updates=['message', 'callback_query', 'my_chat_member',
                                                           'chat_member'])
            except Exception as exc:  # сеть / Telegram — повтор с паузой, без утечки токена
                self.stderr.write(f'Сбой связи ({type(exc).__name__}: {_hide_token(str(exc))}), '
                                  f'повтор через {delay} сек…')
                if options['once']:
                    return
                time.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_SECONDS)
                continue
            delay = RETRY_START_SECONDS
            for update in updates:
                offset = update.update_id + 1
                handle_update(update.json)
            if options['once']:
                return
            if not updates:
                time.sleep(1)

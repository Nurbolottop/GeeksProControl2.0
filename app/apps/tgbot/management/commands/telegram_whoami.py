"""Узнать свой Telegram User ID через тестового бота — без отправки сообщений.

1. Напишите боту /start (в Telegram).
2. manage.py telegram_whoami

Один запрос getUpdates (без offset — обновления НЕ подтверждаются и останутся для
будущего polling), только чтение: кто писал боту в личку. Ничего не отправляет.
Не выполняется, если polling уже запущен (замок polling занят) или режим off.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Показать Telegram id тех, кто написал боту (только чтение, без отправки).'

    def handle(self, *args, **options):
        from apps.common import telegram_guard
        from apps.common.safety import is_protected
        from apps.common.telegram_lock import PollingLocked, acquire_polling_lock, release_polling_lock

        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError('Токен не задан: python3 app/tools/set_telegram_token.py')
        if is_protected(settings.TELEGRAM_BOT_TOKEN):
            raise CommandError('Токен бота 1.0 в 2.0 запрещён.')
        if settings.TELEGRAM_OUTBOUND_MODE == 'off':
            raise CommandError('Режим off блокирует и чтение. Поставьте TELEGRAM_OUTBOUND_MODE=allowlist '
                               '(с пустым списком отправлять некому).')
        try:
            acquire_polling_lock()
        except PollingLocked:
            raise CommandError('Бот уже запущен (polling) — остановите его, затем повторите.') from None
        try:
            telegram_guard.install()
            from apps.graduate_bot.bot import bot

            updates = bot.get_updates(limit=100, timeout=0, allowed_updates=['message'])
        finally:
            release_polling_lock()
        seen = {}
        for update in updates:
            message = getattr(update, 'message', None)
            if message is None or message.chat.type != 'private':
                continue
            user = message.from_user
            seen[user.id] = (user.username or '—', user.first_name or '')
        if not seen:
            self.stdout.write('Сообщений нет: напишите боту /start в Telegram и повторите.')
            return
        allowed = settings.TELEGRAM_TEST_CHAT_IDS
        for user_id, (username, name) in seen.items():
            mark = 'в allowlist' if str(user_id) in allowed else 'не в allowlist'
            self.stdout.write(f'{user_id}  @{username}  {name}  ({mark})')

"""Проверка настроек Telegram без единого запроса в Telegram.

    python manage.py telegram_check

Токен не печатается — только признаки: задан ли, верный ли формат,
не принадлежит ли боту 1.0.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.common import telegram_guard
from apps.common.safety import is_protected, token_format_ok


class Command(BaseCommand):
    help = 'Офлайн-проверка конфигурации Telegram (без запросов к Telegram API)'

    def handle(self, *args, **options):
        token = settings.TELEGRAM_BOT_TOKEN
        rows = [
            ('Окружение (GP2_ENVIRONMENT)', settings.GP2_ENVIRONMENT),
            ('Бот (TELEGRAM_BOT_USERNAME)', '@' + settings.TELEGRAM_BOT_USERNAME if settings.TELEGRAM_BOT_USERNAME else '— не задан'),
            ('Токен', 'задан' if token else 'не задан'),
            ('Формат токена', ('верный' if token_format_ok(token) else 'НЕВЕРНЫЙ') if token else '—'),
            ('Токен бота 1.0', ('ДА — ЗАПРЕЩЁН' if is_protected(token) else 'нет') if token else '—'),
            ('Режим отправки (TELEGRAM_OUTBOUND_MODE)', settings.TELEGRAM_OUTBOUND_MODE),
            ('Тестовые пользователи', str(len(settings.TELEGRAM_TEST_CHAT_IDS))),
            ('Тестовые группы', str(len(settings.TELEGRAM_TEST_GROUP_IDS))),
            ('Защита запросов к Bot API', 'включена' if telegram_guard.is_installed() else 'НЕ ВКЛЮЧЕНА'),
        ]
        width = max(len(label) for label, _ in rows)
        for label, value in rows:
            self.stdout.write(f'{label.ljust(width)}  {value}')
        if settings.TELEGRAM_OUTBOUND_MODE == 'off':
            self.stdout.write(self.style.SUCCESS('Все запросы к Telegram заблокированы (off). Бот не запустится.'))
        elif settings.TELEGRAM_OUTBOUND_MODE == 'allowlist':
            self.stdout.write(self.style.WARNING('Разрешены только тестовые пользователи и группы из allowlist.'))

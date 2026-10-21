"""Единая точка решения «можно ли написать этому человеку в Telegram».

Все сообщения бота идут через apps.graduate_bot.bot.bot.send_message —
и ответы в диалоге, и рассылки из Celery. Бот здесь — GuardedTeleBot:
перед отправкой он спрашивает may_send(). Вне production сообщения по
умолчанию никуда не уходят, а складываются в OUTBOX и пишутся в лог —
это и есть заглушка для разработки и ручной проверки.
"""
import logging
from collections import deque

import telebot
from django.conf import settings

logger = logging.getLogger(__name__)

# Последние «отправленные» в режиме заглушки сообщения: (chat_id, text).
OUTBOX: deque = deque(maxlen=200)


def may_send(chat_id) -> bool:
    mode = settings.TELEGRAM_OUTBOUND_MODE
    if mode == 'on':
        # Celery не проходит системные проверки Django, поэтому запрет
        # «всем — только в production» (gp2.E003) повторяем здесь.
        return settings.GP2_ENVIRONMENT == 'production'
    if mode == 'allowlist':
        chat = str(chat_id)
        return chat in settings.TELEGRAM_TEST_CHAT_IDS or chat in settings.TELEGRAM_TEST_GROUP_IDS
    return False


class GuardedTeleBot(telebot.TeleBot):
    """TeleBot, который пишет только тем, кому разрешено настройками."""

    def send_message(self, chat_id, text, *args, **kwargs):
        if may_send(chat_id):
            return super().send_message(chat_id, text, *args, **kwargs)
        OUTBOX.append((chat_id, text))
        logger.info(
            'Telegram: сообщение не отправлено (TELEGRAM_OUTBOUND_MODE=%s, chat=%s): %.80s',
            settings.TELEGRAM_OUTBOUND_MODE, chat_id, text,
        )
        return None

    def get_file(self, file_id):
        """getFile проверяет telegram_guard (permit 'file'); путь результата
        разрешается для одного скачивания."""
        from apps.common import telegram_guard

        info = super().get_file(file_id)
        if getattr(info, 'file_path', None):
            telegram_guard.permit('path', info.file_path)
        return info

    def download_file(self, file_path):
        """Скачивание идёт мимо apihelper._make_request — проверяем сами."""
        from apps.common import telegram_guard

        telegram_guard.check_download(file_path)
        return super().download_file(file_path)

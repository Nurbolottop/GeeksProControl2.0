"""Входящие обновления: кого бот вообще слушает (этап 8).

Вне production — только allowlist: личные чаты из TELEGRAM_TEST_CHAT_IDS и
группы из TELEGRAM_TEST_GROUP_IDS. Остальные обновления помечаются
«skipped» и не обрабатываются — посторонний не зарегистрируется в stage и
не получит ответ (исходящие и так заблокированы, но и данные не меняются).
В production — все личные чаты; группы — только группа продаж и группы,
привязанные в Control (плюс команда привязки с одноразовым кодом, 8B).
"""
from django.conf import settings


def production() -> bool:
    return settings.GP2_ENVIRONMENT == 'production'


def inbound_allowed(chat_id, chat_type: str, *, binding=False) -> bool:
    if chat_id is None:
        return False
    chat = str(chat_id)
    if chat_type == 'private':
        return production() or chat in settings.TELEGRAM_TEST_CHAT_IDS
    if chat_type in ('group', 'supergroup'):
        if not production():
            return chat in settings.TELEGRAM_TEST_GROUP_IDS
        from apps.tgbot.sales_group import is_sales_chat

        if is_sales_chat(chat) or binding:
            return True
        try:
            from apps.tgbot.models import TelegramGroup

            return TelegramGroup.objects.filter(chat_id=int(chat), status=TelegramGroup.Status.ACTIVE).exists()
        except (LookupError, ImportError, ValueError):
            return False
    return False

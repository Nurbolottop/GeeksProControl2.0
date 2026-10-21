from django.apps import AppConfig


class TgbotConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.tgbot'
    verbose_name = 'Telegram-бот 2.0'

    def ready(self):
        from apps.tgbot import signals  # noqa: F401

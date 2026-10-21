from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.common'
    verbose_name = 'Общее'

    def ready(self):
        # Регистрирует системные проверки gp2.E00x (значения 1.0 в .env).
        from apps.common import safety  # noqa: F401
        # Каждый запрос к Telegram Bot API — через защиту (telegram_guard)
        from apps.common import telegram_guard

        telegram_guard.install()

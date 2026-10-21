from django.apps import AppConfig


class ReserveConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.reserve'
    verbose_name = 'Резерв кадров'

    def ready(self):
        from apps.reserve import signals  # noqa: F401 — тимлиды сразу в резерв

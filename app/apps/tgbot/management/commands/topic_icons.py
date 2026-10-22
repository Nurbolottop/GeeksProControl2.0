"""Иконки тем в привязанных группах проектов (этап 11F): темы не пересоздаются.

  manage.py topic_icons
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Проставить иконки темам привязанных групп проектов.'

    def handle(self, *args, **options):
        from apps.tgbot.groups import apply_icons
        from apps.tgbot.models import TelegramGroup

        for group in TelegramGroup.objects.filter(status=TelegramGroup.Status.ACTIVE):
            self.stdout.write(f'{group.title}: иконок {apply_icons(group)}')

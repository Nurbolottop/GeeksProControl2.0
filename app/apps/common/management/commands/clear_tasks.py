"""Удаляет все задачи проектов.

Автоматические чек-листы («Создать repository», «Провести kickoff» и т.п.)
в работе не используются — команда чистит их разом.

    python manage.py clear_tasks
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.tasks.models import Task


class Command(BaseCommand):
    help = 'Удаляет все задачи во всех проектах'

    @transaction.atomic
    def handle(self, *args, **options):
        total = Task.objects.count()
        by_project = {}
        for task in Task.objects.select_related('project'):
            name = task.project.name if task.project_id else 'без проекта'
            by_project[name] = by_project.get(name, 0) + 1

        Task.objects.all().delete()

        for name, count in sorted(by_project.items(), key=lambda item: -item[1]):
            self.stdout.write(f'  − {name}: {count}')
        self.stdout.write(self.style.SUCCESS(
            f'Удалено задач: {total}. Осталось: {Task.objects.count()}.',
        ))

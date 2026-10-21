"""Привести этапы проектов к их типу.

Этап разработки зависит от типа: у веба Frontend, у мобильного
«Мобильная разработка». Тип часто указывают уже после создания проекта,
и тогда этапы остаются от прежнего набора. Команда чинит такие проекты.

    python manage.py sync_project_stages          # предпросмотр
    python manage.py sync_project_stages --apply
"""
from django.core.management.base import BaseCommand

from apps.projects.models import Project, lifecycle_stages
from apps.projects.services import sync_stages_to_type


class Command(BaseCommand):
    help = 'Приводит набор этапов проектов к их типу (web → Frontend, мобильный → Mobile).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='записать (без флага — только показать)')

    def handle(self, *args, **options):
        apply = options['apply']
        fixed = 0
        for project in Project.objects.select_related('project_type').order_by('name'):
            desired = [str(key) for key in lifecycle_stages(project.project_type)]
            current = [stage.key for stage in project.stages.order_by('order')]
            if current == desired:
                continue
            missing = [key for key in desired if key not in current]
            extra = [key for key in current if key not in desired]
            self.stdout.write(
                f'  {project.name} ({project.project_type or "тип не указан"}): '
                f'не хватает {missing or "—"}, лишние {extra or "—"}',
            )
            if apply:
                sync_stages_to_type(project)
            fixed += 1
        verb = 'Исправлено' if apply else 'Будет исправлено'
        self.stdout.write(self.style.SUCCESS(f'{verb} проектов: {fixed}.'))
        if not apply:
            self.stdout.write('Это предпросмотр. Для записи: --apply')

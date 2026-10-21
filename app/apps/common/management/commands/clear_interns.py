"""Очищает список стажёров, оставляя ПМ, тимлидов и указанные проекты.

Кого оставляем всегда: у кого есть роль PM или «Тимлид» хотя бы в одной
команде. Плюс — всех участников проектов из --keep-projects.

    python manage.py clear_interns --dry-run
    python manage.py clear_interns --keep-projects Омур ОБА
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.interns.models import Intern
from apps.teams.models import TeamMember, TeamRole

KEEP_ROLES = [TeamRole.PROJECT_MANAGER, TeamRole.TEAM_LEAD]


class Command(BaseCommand):
    help = 'Удаляет стажёров, кроме ПМ, тимлидов и команд указанных проектов'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='показать, кого удалит, но ничего не менять',
        )
        parser.add_argument(
            '--keep-projects', nargs='*', default=[],
            help='названия проектов, чьи команды остаются целиком',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        keep_ids = set(
            TeamMember.objects.filter(role__in=KEEP_ROLES)
            .exclude(intern__isnull=True)
            .values_list('intern_id', flat=True),
        )
        projects = options['keep_projects']
        if projects:
            keep_ids |= set(
                TeamMember.objects.filter(project__name__in=projects)
                .exclude(intern__isnull=True)
                .values_list('intern_id', flat=True),
            )
            self.stdout.write(
                'Команды целиком остаются: ' + ', '.join(projects),
            )

        drop = Intern.objects.exclude(pk__in=keep_ids).order_by('full_name')
        count = drop.count()

        for intern in drop:
            self.stdout.write(f'  − {intern.full_name}')
        self.stdout.write(f'Удаляются: {count}, остаются: {len(keep_ids)}')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Пробный запуск — ничего не удалено.'))
            return

        drop.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Удалено стажёров: {count}. Осталось: {Intern.objects.count()}.',
        ))

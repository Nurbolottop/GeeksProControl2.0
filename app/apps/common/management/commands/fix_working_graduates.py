"""Снимает метку выпускника с тех, кто на самом деле работает.

Пока завершение одного проекта делало выпускником всю команду, люди с
двумя-тремя проектами попадали в «Выпускники», не переставая работать.
Сам баг исправлен (apps.teams.services.still_on_a_live_project), эта
команда убирает его следы: --apply, без него — только показывает.
"""
from django.core.management.base import BaseCommand

from apps.interns.models import GraduateStatus, Intern, InternStatus
from apps.projects.models import ProjectStatus
from apps.teams.models import TeamMember


class Command(BaseCommand):
    help = 'Снять метку выпускника с тех, кто занят на идущем проекте'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Сохранить изменения (без флага — только показать).',
        )

    def handle(self, *args, **options):
        working = set(
            TeamMember.objects.filter(
                status=TeamMember.Status.ACTIVE, intern__isnull=False,
                project__status=ProjectStatus.ACTIVE,
            ).values_list('intern_id', flat=True),
        )
        stuck = list(
            Intern.objects.exclude(graduate_status='').filter(pk__in=working),
        )
        for intern in stuck:
            projects = ', '.join(
                member.project.name for member in
                intern.team_memberships.filter(
                    status=TeamMember.Status.ACTIVE,
                    project__status=ProjectStatus.ACTIVE,
                ).select_related('project')
            )
            self.stdout.write(f'  − {intern.full_name}: работает на {projects}')
            if options['apply']:
                fields = ['graduate_status']
                intern.graduate_status = ''
                if intern.status == InternStatus.READY:
                    intern.status = InternStatus.ACTIVE
                    fields.append('status')
                intern.save(update_fields=[*fields, 'updated_at'])

        remaining = Intern.objects.filter(
            graduate_status=GraduateStatus.PENDING,
        ).count()
        action = 'Снята метка выпускника' if options['apply'] else 'Будет снята метка'
        self.stdout.write(self.style.SUCCESS(
            f'{action}: {len(stuck)}. Останется выпускников на проверке: '
            f'{remaining - (len(stuck) if options["apply"] else 0)}.',
        ))

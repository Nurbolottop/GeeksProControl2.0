"""Проставить должности по уже назначенным ролям в командах.

Должность появилась позже самих назначений: тимлидом человек был только
внутри проекта. Эта команда переносит то, что уже есть, в карточку —
тимлид на любом проекте (в том числе завершённом) становится тимлидом по
должности, ПМ — проект-менеджером.

Без --apply только показывает, кого и как пометит.
"""
from django.core.management.base import BaseCommand

from apps.interns.models import Intern, Position
from apps.teams.models import TeamMember, TeamRole


class Command(BaseCommand):
    help = 'Проставить должности (тимлид/ПМ) по ролям в командах.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Записать в базу.')

    def handle(self, *args, **options):
        leads = set(
            TeamMember.objects.filter(role=TeamRole.TEAM_LEAD)
            .exclude(intern__isnull=True).values_list('intern_id', flat=True),
        )
        pms = set(
            TeamMember.objects.filter(role=TeamRole.PROJECT_MANAGER)
            .exclude(intern__isnull=True).values_list('intern_id', flat=True),
        )
        # Тимлид сильнее: если человек где-то вёл как ПМ, а где-то как
        # тимлид, должность у него тимлидская.
        pms -= leads

        planned = []
        for pk_set, position in ((leads, Position.TEAM_LEAD), (pms, Position.PM)):
            for intern in Intern.objects.filter(pk__in=pk_set).exclude(position=position):
                planned.append((intern, position))

        for intern, position in planned:
            self.stdout.write(
                f'  {intern.full_name}: {intern.get_position_display()} → '
                f'{Position(position).label}'
            )
        self.stdout.write(f'Будет изменено: {len(planned)}')
        if not options['apply']:
            self.stdout.write('Предпросмотр. Для записи: --apply')
            return
        for intern, position in planned:
            intern.position = position
            intern.save(update_fields=['position', 'updated_at'])
        self.stdout.write(self.style.SUCCESS(f'Готово. Изменено: {len(planned)}'))

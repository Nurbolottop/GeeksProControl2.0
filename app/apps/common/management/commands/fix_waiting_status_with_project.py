"""Чинит статус «Ожидает стажировки»/«Готов к распределению» у тех, кто
на самом деле уже на проекте — статус никогда не обновлялся при
назначении проекта (ни при пакетном назначении, ни через «+ Добавить
в проект», пока это не починили). Идемпотентна — можно перезапускать.

    python manage.py fix_waiting_status_with_project
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.interns.models import Intern, InternStatus
from apps.teams.models import TeamMember


class Command(BaseCommand):
    help = 'Переводит в «Активный» тех, у кого уже есть проект, но статус висит на «Ожидает»'

    @transaction.atomic
    def handle(self, *args, **options):
        busy_ids = TeamMember.objects.filter(
            status=TeamMember.Status.ACTIVE,
        ).values_list('intern_id', flat=True)
        mismatched = Intern.objects.filter(
            pk__in=busy_ids,
            status__in=[InternStatus.WAITING, InternStatus.READY],
        )
        count = 0
        for intern in mismatched:
            self.stdout.write(f'  {intern.full_name}')
            intern.status = InternStatus.ACTIVE
            intern.save(update_fields=['status', 'updated_at'])
            count += 1
        self.stdout.write(self.style.SUCCESS(f'Исправлено: {count}.'))

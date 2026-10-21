"""Убирает дубли участия: один человек — одна запись в команде проекта.

    python manage.py dedupe_members
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.teams.models import TeamMember


class Command(BaseCommand):
    help = 'Удаляет повторные записи участника в одной и той же команде'

    @transaction.atomic
    def handle(self, *args, **options):
        seen = set()
        removed = 0
        for member in TeamMember.objects.select_related(
            'intern', 'project',
        ).order_by('pk'):
            key = (member.project_id, member.group_id, member.intern_id)
            if key in seen:
                self.stdout.write(self.style.WARNING(
                    f'  − дубль: {member.person_name} в '
                    f'«{member.project.name if member.project_id else "—"}»',
                ))
                member.delete()
                removed += 1
            else:
                seen.add(key)
        self.stdout.write(self.style.SUCCESS(
            f'Удалено дублей: {removed}. Записей в командах: '
            f'{TeamMember.objects.count()}.',
        ))

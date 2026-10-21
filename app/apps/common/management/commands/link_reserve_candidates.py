"""Связать карточки резерва кадров с карточками людей.

Большая часть резерва — наши же тимлиды, но анкеты заводились отдельно и
ни к кому не привязаны. Связываем по совпавшему телефону, почте или
telegram — только однозначные совпадения.

    python manage.py link_reserve_candidates          # предпросмотр
    python manage.py link_reserve_candidates --apply
"""
from django.core.management.base import BaseCommand

from apps.reserve import services
from apps.reserve.models import ReserveCandidate


class Command(BaseCommand):
    help = 'Связывает кандидатов резерва с карточками людей по контактам.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='записать связи (без флага — только показать)')

    def handle(self, *args, **options):
        apply = options['apply']
        linked, skipped = 0, 0
        for candidate in ReserveCandidate.objects.filter(intern__isnull=True).order_by('full_name'):
            person = services.find_intern_for(candidate)
            if person is None:
                skipped += 1
                self.stdout.write(f'  без пары: {candidate.full_name}')
                continue
            self.stdout.write(f'  {candidate.full_name} → {person.full_name} (#{person.pk})')
            if apply:
                services.link_to_intern(candidate)
            linked += 1
        verb = 'Связано' if apply else 'Будет связано'
        self.stdout.write(self.style.SUCCESS(f'{verb}: {linked}. Без пары: {skipped}.'))
        if not apply:
            self.stdout.write('Это предпросмотр. Для записи: --apply')

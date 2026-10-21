"""Все тимлиды — в резерв кадров.

Новые тимлиды попадают туда сами в момент назначения. Команда нужна для
тех, кто стал тимлидом раньше: привязывает их анкету, если она уже есть,
иначе заводит карточку из данных тимлида.

    python manage.py add_leads_to_reserve          # предпросмотр
    python manage.py add_leads_to_reserve --apply
"""
from django.core.management.base import BaseCommand

from apps.interns.models import Intern
from apps.reserve import services
from apps.reserve.models import ReserveCandidate
from apps.teams.selectors import lead_intern_ids


class Command(BaseCommand):
    help = 'Добавляет в резерв кадров всех тимлидов, кого там ещё нет.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='записать (без флага — только показать)')

    def handle(self, *args, **options):
        apply = options['apply']
        counts = {'already': 0, 'link': 0, 'create': 0}
        for person in Intern.objects.filter(pk__in=lead_intern_ids()).order_by('full_name'):
            if ReserveCandidate.objects.filter(intern=person).exists():
                counts['already'] += 1
                continue
            match = services.find_candidate_for(person)
            if match is not None:
                counts['link'] += 1
                self.stdout.write(f'  связать: {person.full_name} ← анкета «{match.full_name}»')
            else:
                counts['create'] += 1
                self.stdout.write(f'  завести: {person.full_name}')
            if apply:
                services.ensure_lead_in_reserve(person)
        verb = 'Сделано' if apply else 'Будет'
        self.stdout.write(self.style.SUCCESS(
            f"{verb}: заведено {counts['create']}, связано {counts['link']}. "
            f"Уже в резерве: {counts['already']}.",
        ))
        if not apply:
            self.stdout.write('Это предпросмотр. Для записи: --apply')

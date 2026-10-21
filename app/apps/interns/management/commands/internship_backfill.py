"""Перенос периодов стажировок из существующих данных (UI-04A).

  manage.py internship_backfill           — отчёт, ничего не меняет
  manage.py internship_backfill --apply   — создать периоды

Только для людей без единого периода. Номер — «Какая по счёту стажировка»
из карточки; начало — дата начала стажировки из карточки, иначе самая
ранняя дата вступления в проект, иначе пусто. Дату окончания не угадываем:
«Трудоустроен» и «Выбыл» закрывают период без даты (причина видна),
остальные статусы оставляют период открытым. Тимлиды — сотрудники, пропускаются.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Min


class Command(BaseCommand):
    help = 'Периоды стажировок для людей, у которых их ещё нет.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, apply=False, **options):
        from apps.interns.models import Intern, Internship, InternStatus
        from apps.teams.selectors import lead_intern_ids

        people = (Intern.objects.exclude(pk__in=lead_intern_ids()).filter(internships__isnull=True)
                  .annotate(first_join=Min('team_memberships__joined_at')).order_by('full_name'))
        closed = {InternStatus.EMPLOYED: Internship.EndReason.EMPLOYED, InternStatus.DROPPED: Internship.EndReason.LEFT}
        rows = []
        for person in people:
            if not (person.internship_start_date or person.first_join or person.team_memberships.exists()):
                continue  # ни карточки, ни участий — стажировки ещё не было
            start = person.internship_start_date or person.first_join
            source = Internship.Source.CARD if person.internship_start_date else Internship.Source.JOIN
            rows.append((person, start, source, closed.get(person.status, '')))
        for person, start, source, reason in rows:
            self.stdout.write(f'  {person.full_name}: №{person.internship_attempt}, начало '
                              f'{start:%d.%m.%Y}' if start else f'  {person.full_name}: №{person.internship_attempt}, начало неизвестно')
        if not apply:
            self.stdout.write(f'Будет создано периодов: {len(rows)}. Ничего не изменено — запустите с --apply.')
            return
        created = 0
        with transaction.atomic():
            for person, start, source, reason in rows:
                if person.internships.exists():
                    continue
                Internship.objects.create(intern=person, number=max(person.internship_attempt or 1, 1),
                                          started_on=start, source=source, end_reason=reason,
                                          note='перенесено из существующих данных')
                created += 1
        self.stdout.write(self.style.SUCCESS(f'Создано периодов: {created}'))

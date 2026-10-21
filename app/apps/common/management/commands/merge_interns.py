"""Слить дубль стажёра в основную карточку.

Типовой случай: старая карточка с проектом, оценками и табелем, но без
контактов, и новая анкета самозаполнения с контактами, но без проекта.
Оставляем ту, где накоплена история, а личные данные берём из анкеты.

    python manage.py merge_interns --keep 684 --duplicate 884          # предпросмотр
    python manage.py merge_interns --keep 684 --duplicate 884 --apply
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.attendance.models import Attendance, GroupMeeting, WorkScore
from apps.interns.models import Intern, InternEvaluation, ProfileFormSubmission
from apps.teams.models import TeamMember

# Личные данные: при --prefer duplicate непустое значение дубля побеждает
PERSONAL_FIELDS = (
    'full_name', 'phone', 'email', 'telegram', 'city', 'branch',
    'education_end_date', 'internship_start_date',
)


class Command(BaseCommand):
    help = 'Сливает карточку-дубль стажёра в основную и удаляет дубль.'

    def add_arguments(self, parser):
        parser.add_argument('--keep', type=int, required=True,
                            help='id карточки, которая остаётся')
        parser.add_argument('--duplicate', type=int, required=True,
                            help='id карточки-дубля, её удалим')
        parser.add_argument(
            '--prefer', choices=['keep', 'duplicate'], default='duplicate',
            help='чьи личные данные важнее при конфликте (по умолчанию — дубля, '
                 'это обычно свежая анкета)',
        )
        parser.add_argument(
            '--skip', default='',
            help='поля, которые не переносить из дубля, через запятую '
                 '(например: city,telegram — когда в анкете мусор)',
        )
        parser.add_argument('--apply', action='store_true',
                            help='записать изменения (без флага — предпросмотр)')

    def handle(self, *args, **options):
        keep_pk, dup_pk = options['keep'], options['duplicate']
        if keep_pk == dup_pk:
            raise CommandError('--keep и --duplicate должны отличаться.')
        try:
            keep = Intern.objects.get(pk=keep_pk)
            dup = Intern.objects.get(pk=dup_pk)
        except Intern.DoesNotExist as exc:
            raise CommandError(f'Карточка не найдена: {exc}')

        self.stdout.write(f'Оставляем: {keep.pk} «{keep.full_name}»')
        self.stdout.write(f'Удаляем:   {dup.pk} «{dup.full_name}»')

        skip = {name.strip() for name in options['skip'].split(',') if name.strip()}
        unknown = skip - set(PERSONAL_FIELDS)
        if unknown:
            raise CommandError(f'Неизвестные поля в --skip: {", ".join(sorted(unknown))}')

        changes = {}
        for field in PERSONAL_FIELDS:
            if field in skip:
                self.stdout.write(f'  {field}: пропускаем по --skip')
                continue
            mine, theirs = getattr(keep, field), getattr(dup, field)
            if not theirs or mine == theirs:
                continue
            if mine and options['prefer'] == 'keep':
                self.stdout.write(f'  {field}: оставляем {mine!r} (в дубле {theirs!r})')
                continue
            changes[field] = theirs
            self.stdout.write(f'  {field}: {mine!r} → {theirs!r}')
        if keep.specialization is None and dup.specialization is not None:
            changes['specialization'] = dup.specialization
        if keep.user is None and dup.user is not None:
            changes['user'] = dup.user

        moves = {
            'проекты': TeamMember.objects.filter(intern=dup),
            'оценки': InternEvaluation.objects.filter(intern=dup),
            'отметки посещаемости': Attendance.objects.filter(intern=dup),
            'оценки активности': WorkScore.objects.filter(intern=dup),
            'собрания (ведущий)': GroupMeeting.objects.filter(host=dup),
            'анкеты': ProfileFormSubmission.objects.filter(intern=dup),
        }
        for label, qs in moves.items():
            count = qs.count()
            if count:
                self.stdout.write(f'  переносим {label}: {count}')

        if not options['apply']:
            self.stdout.write('Это предпросмотр. Для записи: --apply')
            return

        with transaction.atomic():
            # у Attendance и WorkScore ключ (собрание, стажёр) уникален: если
            # отметка на это собрание уже есть у основной карточки, запись
            # дубля не переносим, а удаляем — она дублирует ту же отметку
            for model in (Attendance, WorkScore):
                taken = set(
                    model.objects.filter(intern=keep).values_list('meeting_id', flat=True),
                )
                clash = model.objects.filter(intern=dup, meeting_id__in=taken)
                if clash.exists():
                    self.stdout.write(
                        f'  {model._meta.verbose_name_plural}: у обоих есть отметки '
                        f'на одни и те же собрания ({clash.count()}) — оставляем '
                        f'отметки основной карточки',
                    )
                    clash.delete()
            for label, qs in moves.items():
                field = 'host' if label.startswith('собрания') else 'intern'
                qs.update(**{field: keep})
            # обе карточки могли числиться на одном проекте — оставляем одну запись
            seen = set()
            for member in TeamMember.objects.filter(intern=keep).order_by('pk'):
                key = (member.project_id, member.role, member.status)
                if key in seen:
                    self.stdout.write(
                        f'  убираем задвоенное участие в проекте: {member.project}',
                    )
                    member.delete()
                    continue
                seen.add(key)
            for field, value in changes.items():
                setattr(keep, field, value)
            keep.save()
            dup.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Готово: {dup_pk} слит в {keep_pk} «{keep.full_name}».',
        ))

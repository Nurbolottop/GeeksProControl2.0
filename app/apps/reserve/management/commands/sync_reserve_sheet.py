"""Выгрузка резерва кадров в общую Google-таблицу вручную.

Без --apply только показывает, что уйдёт на каждый лист направления —
удобно проверить распределение и доступ сервисного аккаунта.
"""
from django.core.management.base import BaseCommand

from apps.reserve import gsheets


class Command(BaseCommand):
    help = 'Переписать листы направлений в Google-таблице резерва (по умолчанию — предпросмотр).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Записать в таблицу.')

    def handle(self, *args, **options):
        groups = gsheets.rows_by_direction()
        total = sum(len(rows) for rows in groups.values())
        self.stdout.write(f'Кандидатов к выгрузке: {total}')
        for key in sorted(groups):
            self.stdout.write(f'\n— лист {key}: {len(groups[key])}')
            for row in groups[key]:
                name, direction, projects, _, end, phone, telegram = row
                self.stdout.write(
                    f'  {name} | {direction} | {projects or "—"} | '
                    f'{end or "—"} | {phone or "—"} | {telegram or "—"}'
                )

        skipped = [
            c.full_name for c in gsheets.candidates_for_sheet()
            if not gsheets.tab_key(gsheets.direction_of(c))
        ]
        if skipped:
            self.stdout.write(self.style.WARNING(
                f'\nБез подходящего листа ({len(skipped)}): ' + ', '.join(skipped)
            ))

        if not gsheets.is_configured():
            self.stdout.write(self.style.WARNING(
                '\nGoogle-таблица не настроена: нужны GOOGLE_SHEETS_CREDENTIALS_FILE '
                '(файл ключа сервисного аккаунта) и RESERVE_SHEET_ID.'
            ))
            return
        if not options['apply']:
            self.stdout.write('\nПредпросмотр. Для записи в таблицу: --apply')
            return
        count = gsheets.push(groups)
        self.stdout.write(self.style.SUCCESS(f'\nВ таблицу записано строк: {count}'))

"""Список записей, по которым нельзя достоверно установить выпуск.

Только чтение: ничего в базе не меняет. Такие люди не считаются
выпускниками на Dashboard, пока руководитель их не сверит.

    python manage.py graduate_reconciliation
"""
from django.core.management.base import BaseCommand

from apps.dashboard.graduation import compute


class Command(BaseCommand):
    help = 'Показывает записи стажёров, требующие сверки выпуска (без изменений в БД)'

    def handle(self, *args, **options):
        result = compute()
        self.stdout.write(f'Подтверждённых выпускников: {len(result.ids)}')
        if not result.review:
            self.stdout.write(self.style.SUCCESS('Записей на сверку нет.'))
            return
        self.stdout.write(self.style.WARNING(f'Требуют сверки: {len(result.review)}'))
        for item in result.review:
            project = f' — {item["project"]}' if item['project'] else ''
            self.stdout.write(f'  #{item["intern_id"]} {item["name"]}{project}: {item["reason"]}')

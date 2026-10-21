"""Изменения в 2.0 после переключения (этап 11A) — основа решения об откате.

Откат к 1.0 = возврат к замороженной базе 1.0; всё, что создано или изменено в
2.0 после переключения, при этом теряется. Поэтому перед откатом выгружаются
все такие записи (по полям created_at / updated_at / received_at / changed_at),
руководитель согласует, что и как переносится в 1.0 вручную.

    manage.py post_cutover_changes --since 2026-11-01T09:00:00+06:00 --output /root/backups/delta.json
Файл содержит персональные данные: права 600, хранить вместе с бэкапами.
"""
import datetime
import json
from collections import Counter
from pathlib import Path

from django.apps import apps
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError

TIME_FIELDS = ('updated_at', 'created_at', 'received_at', 'changed_at')


def changes_since(since):
    result, summary = [], Counter()
    for model in apps.get_models():
        if model._meta.app_label in ('sessions', 'admin', 'contenttypes', 'auth') or model._meta.proxy:
            continue
        names = {f.name for f in model._meta.concrete_fields}
        fields = [f for f in TIME_FIELDS if f in names]
        if not fields:
            continue
        from django.db.models import Q

        query = Q()
        for field in fields:
            query |= Q(**{f'{field}__gte': since})
        rows = list(model._base_manager.filter(query))
        if rows:
            summary[model._meta.label] = len(rows)
            result.extend(json.loads(serializers.serialize('json', rows)))
    return result, summary


class Command(BaseCommand):
    help = 'Выгрузка записей, созданных или изменённых в 2.0 после момента переключения.'

    def add_arguments(self, parser):
        parser.add_argument('--since', required=True, help='Момент переключения, ISO 8601 с часовым поясом.')
        parser.add_argument('--output', help='JSON-файл выгрузки (иначе — только сводка).')

    def handle(self, *args, **options):
        try:
            since = datetime.datetime.fromisoformat(options['since'])
        except ValueError:
            raise CommandError('Неверный --since: нужен ISO 8601, например 2026-11-01T09:00:00+06:00') from None
        if since.tzinfo is None:
            raise CommandError('Укажите часовой пояс в --since (например, +06:00).')
        rows, summary = changes_since(since)
        for label, count in sorted(summary.items()):
            self.stdout.write(f'{label}: {count}')
        self.stdout.write(f'Всего записей после переключения: {len(rows)}')
        if options['output']:
            path = Path(options['output'])
            path.write_text(json.dumps({'since': since.isoformat(), 'summary': summary, 'records': rows},
                                       ensure_ascii=False, indent=1, default=str))
            path.chmod(0o600)
            self.stdout.write(f'Выгрузка: {path} (права 600 — в ней персональные данные)')

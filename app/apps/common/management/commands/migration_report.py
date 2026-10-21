"""Отчёт сверки для переноса 1.0 → 2.0 (этап 10B, docs/migration-plan.md §5).

Только чтение своей базы (той, к которой подключён этот экземпляр): к базе
1.0 команда не подключается. На репетиции её запускают дважды — на базе,
восстановленной из дампа 1.0 до `migrate` (кодом 1.0 или 2.0 с --legacy), и
после `migrate` кодом 2.0, — и сравнивают: `migration_report --compare a.json b.json`.

    manage.py migration_report --output /root/backups/report-before.json [--media]
    manage.py migration_report --compare before.json after.json

Персональные данные в отчёт не пишутся: только количества, контрольные
суммы файлов и идентификаторы записей с проблемами.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models
from django.db.models import Count

# (метка, модель, поле среза или None)
SLICES = [
    ('users', 'accounts.User', 'role'),
    ('interns', 'interns.Intern', 'status'),
    ('interns_graduate_status', 'interns.Intern', 'graduate_status'),
    ('projects', 'projects.Project', 'status'),
    ('clients', 'clients.Client', None),
    ('team_members', 'teams.TeamMember', 'status'),
    ('team_roles', 'teams.TeamMember', 'role'),
    ('documents', 'documents.Document', None),
    ('internship_requests', 'interns.InternshipRequest', 'kind'),
    ('internships', 'interns.Internship', None),
    ('warnings', 'interns.InternWarning', None),
    ('audit_events', 'audit.AuditLog', None),
    ('weekly_reports_legacy', 'reports.WeeklyReport', None),
    ('monthly_reports', 'reports.MonthlyReport', None),
    ('project_reports', 'projects.ProjectReport', None),
    ('evaluations_7_criteria', 'interns.InternEvaluation', None),
    ('work_scores', 'attendance.WorkScore', None),
    ('group_meetings', 'attendance.GroupMeeting', 'status'),
    ('attendance_marks', 'attendance.Attendance', 'status'),
    ('reserve', 'reserve.ReserveCandidate', 'status'),
    ('tasks', 'tasks.Task', 'status'),
]


def _model(label):
    try:
        return apps.get_model(label)
    except LookupError:
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _safe(report, key, func):
    """Схема 1.0 (до migrate) не знает новых таблиц 2.0: такой показатель — None
    и попадает в unavailable, а не роняет отчёт."""
    from django.db import DatabaseError, transaction

    try:
        with transaction.atomic():
            return func()
    except (DatabaseError, LookupError, AttributeError):
        report['unavailable'].append(key)
        return None


def build_report(include_media=False) -> dict:
    from apps.dashboard.metrics import graduate_ids
    from apps.interns.models import Intern

    report = {'counts': {}, 'slices': {}, 'checks': {}, 'unavailable': []}
    for label, model_label, field in SLICES:
        model = _model(model_label)
        if model is None:
            report['counts'][label] = None
            continue
        qs = model._base_manager.all()
        report['counts'][label] = _safe(report, label, qs.count)
        if field and report['counts'][label] is not None:
            report['slices'][label] = _safe(report, f'{label}:{field}', lambda qs=qs, field=field: {
                str(k or '—'): v for k, v in qs.values_list(field).annotate(n=Count('pk')).values_list(field, 'n')})
    report['counts']['graduates_derived'] = _safe(report, 'graduates_derived', lambda: len(graduate_ids()))
    report['counts']['interns_with_telegram'] = _safe(
        report, 'interns_with_telegram', lambda: Intern._base_manager.exclude(telegram_chat_id=None).count())

    # Правила связи профилей: дубли ломают автоматическую привязку бота и сверку продаж
    def telegram_dups():
        tg = Counter(Intern._base_manager.exclude(telegram_chat_id=None).values_list('telegram_chat_id', flat=True))
        return sorted(str(k) for k, v in tg.items() if v > 1)[:200]

    def phone_dups():
        from apps.interns.people import _phone_digits

        phones = Counter(p[-9:] for p in Intern._base_manager.exclude(phone='').annotate(d=_phone_digits())
                         .values_list('d', flat=True) if p and len(p) >= 9)
        return sum(1 for v in phones.values() if v > 1)

    def open_periods():
        return list(_model('interns.Internship')._base_manager.filter(ended_on__isnull=True).values('intern_id')
                    .annotate(n=Count('pk')).filter(n__gt=1).values_list('intern_id', flat=True)[:200])

    def no_city():
        return Intern._base_manager.filter(branch='', city='').count()

    def no_direction():
        return Intern._base_manager.filter(specialization__isnull=True).count()

    def ambiguous_left():
        return _model('teams.TeamMember')._base_manager.filter(status='left', left_reason='').count()

    for key, func in (('duplicate_telegram_chat_ids', telegram_dups), ('duplicate_phone_groups', phone_dups),
                      ('open_internship_periods_conflicts', open_periods), ('interns_without_city', no_city),
                      ('interns_without_direction', no_direction), ('left_members_without_reason', ambiguous_left)):
        report['checks'][key] = _safe(report, key, func)
    with connection.cursor() as cursor:
        cursor.execute('SELECT app, name FROM django_migrations ORDER BY app, name')
        report['migrations'] = [f'{a}.{n}' for a, n in cursor.fetchall()]
    report['checks']['sequences_behind'] = _sequences_behind()
    if include_media:
        report['media'] = _media_manifest()
    return report


def _sequences_behind() -> list[str]:
    """Таблицы, где sequence id меньше max(id) — после pg_restore вставки упадут."""
    behind = []
    existing = set(connection.introspection.table_names())
    with connection.cursor() as cursor:
        for model in apps.get_models():
            if model._meta.db_table not in existing:
                continue
            pk = model._meta.pk
            if not isinstance(pk, (models.AutoField, models.BigAutoField)) or model._meta.proxy:
                continue
            table = model._meta.db_table
            cursor.execute("SELECT pg_get_serial_sequence(%s, %s)", [table, pk.column])
            seq = cursor.fetchone()[0]
            if not seq:
                continue
            cursor.execute(f'SELECT COALESCE(MAX("{pk.column}"), 0) FROM "{table}"')
            max_id = cursor.fetchone()[0]
            cursor.execute(f'SELECT last_value, is_called FROM {seq}')
            last, called = cursor.fetchone()
            if max_id and (last < max_id or (last == max_id and not called)):
                behind.append(table)
    return behind


def _media_manifest() -> dict:
    root = Path(settings.MEDIA_ROOT)
    files, missing = {}, []
    existing = set(connection.introspection.table_names())
    for model in apps.get_models():
        if model._meta.db_table not in existing:
            continue
        file_fields = [f for f in model._meta.get_fields() if isinstance(f, models.FileField)]
        for field in file_fields:
            for name in model._base_manager.exclude(**{field.name: ''}).values_list(field.name, flat=True):
                if not name:
                    continue
                path = root / name
                if path.is_file():
                    files[name] = _sha256(path)
                else:
                    missing.append(f'{model._meta.label}.{field.name}')
    return {'files': len(files), 'sha256': files, 'missing_by_field': dict(Counter(missing))}


def compare(before: dict, after: dict) -> list[str]:
    problems = []
    for key, value in before.get('counts', {}).items():
        if value is not None and after.get('counts', {}).get(key) != value:
            problems.append(f'количество {key}: {value} → {after.get("counts", {}).get(key)}')
    for key, value in before.get('slices', {}).items():
        if after.get('slices', {}).get(key) != value:
            problems.append(f'срез {key} отличается')
    missing_migrations = set(before.get('migrations', [])) - set(after.get('migrations', []))
    if missing_migrations:
        problems.append(f'миграций 1.0 нет в 2.0: {len(missing_migrations)}')
    for key in ('duplicate_telegram_chat_ids', 'open_internship_periods_conflicts', 'sequences_behind'):
        if after.get('checks', {}).get(key):  # блокеры переключения
            problems.append(f'проверка {key}: {len(after["checks"][key])}')
    if 'media' in before and 'media' in after:
        changed = [n for n, h in before['media']['sha256'].items() if after['media']['sha256'].get(n) != h]
        if changed:
            problems.append(f'файлы media отличаются или отсутствуют: {len(changed)}')
        if after['media']['missing_by_field']:
            problems.append(f'файлы из FileField не найдены: {after["media"]["missing_by_field"]}')
    return problems


class Command(BaseCommand):
    help = 'Отчёт сверки переноса 1.0 → 2.0 (только чтение; без персональных данных).'

    def add_arguments(self, parser):
        parser.add_argument('--output', help='Куда записать JSON-отчёт.')
        parser.add_argument('--media', action='store_true', help='Посчитать sha256 всех файлов media.')
        parser.add_argument('--compare', nargs=2, metavar=('ДО', 'ПОСЛЕ'), help='Сравнить два отчёта.')

    def handle(self, *args, **options):
        if options['compare']:
            before, after = (json.loads(Path(p).read_text()) for p in options['compare'])
            problems = compare(before, after)
            for line in problems:
                self.stdout.write(f'РАСХОЖДЕНИЕ: {line}')
            if problems:
                raise CommandError(f'Расхождений: {len(problems)} — переключение запрещено (план, раздел 5).')
            self.stdout.write(self.style.SUCCESS('Сверка без расхождений.'))
            return
        report = build_report(include_media=options['media'])
        text = json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True)
        if options['output']:
            Path(options['output']).write_text(text)
            self.stdout.write(f'Отчёт: {options["output"]} (записей: {sum(v or 0 for v in report["counts"].values())})')
        else:
            self.stdout.write(text)

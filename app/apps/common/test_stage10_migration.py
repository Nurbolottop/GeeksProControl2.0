"""Этап 10B: команда сверки переноса (синтетические данные, только чтение)."""
import json
import tempfile
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.common.management.commands.migration_report import build_report, compare
from apps.interns.models import Intern
from apps.projects.models import Project


class MigrationReportTests(TestCase):
    def test_report_compare_and_profile_link_risks(self):
        Project.objects.create(name='Синтетический')
        a = Intern.objects.create(full_name='Синт Один', phone='0700111222', telegram_chat_id=555)
        Intern.objects.create(full_name='Синт Два', phone='+996 700 111 222', telegram_chat_id=555)
        report = build_report()
        self.assertEqual(report['counts']['projects'], 1)
        self.assertEqual(report['checks']['duplicate_telegram_chat_ids'], ['555'])
        self.assertEqual(report['checks']['duplicate_phone_groups'], 1)
        self.assertIn('interns.0001_initial', report['migrations'])
        self.assertEqual(report['checks']['sequences_behind'], [])
        self.assertNotIn('Синт Один', json.dumps(report, ensure_ascii=False))  # без персональных данных
        after = json.loads(json.dumps(report))
        after['counts']['projects'] = 0
        problems = compare(report, after)
        self.assertTrue(any('projects' in p for p in problems))
        a.delete()
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / 'a.json', Path(tmp) / 'b.json'
            call_command('migration_report', output=str(first))
            call_command('migration_report', output=str(second))
            Intern.objects.filter(telegram_chat_id=555).update(telegram_chat_id=556)
            call_command('migration_report', compare=[str(first), str(second)])
            Project.objects.all().delete()
            call_command('migration_report', output=str(second))
            with self.assertRaises(CommandError):
                call_command('migration_report', compare=[str(first), str(second)])


class PostCutoverChangesTests(TestCase):
    def test_exports_only_changes_after_cutover(self):
        import datetime

        from django.utils import timezone

        from apps.common.management.commands.post_cutover_changes import changes_since

        old = Project.objects.create(name='До переключения')
        Project.objects.filter(pk=old.pk).update(created_at=timezone.now() - datetime.timedelta(days=2),
                                                 updated_at=timezone.now() - datetime.timedelta(days=2))
        cutover = timezone.now() - datetime.timedelta(hours=1)
        Project.objects.create(name='После переключения')
        rows, summary = changes_since(cutover)
        names = [r['fields'].get('name') for r in rows if r['model'] == 'projects.project']
        self.assertEqual(names, ['После переключения'])
        self.assertEqual(summary['projects.Project'], 1)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'delta.json'
            call_command('post_cutover_changes', since=cutover.isoformat(), output=str(out))
            self.assertEqual(oct(out.stat().st_mode & 0o777), '0o600')
            self.assertEqual(json.loads(out.read_text())['summary']['projects.Project'], 1)
        with self.assertRaises(CommandError):
            call_command('post_cutover_changes', since='2026-11-01T09:00:00')  # без часового пояса


class LegacySeedGuardTests(TestCase):
    def test_refuses_outside_synthetic_database(self):
        from django.db import connection

        self.assertNotIn('synth', connection.settings_dict['NAME'])
        with self.assertRaisesMessage(CommandError, 'synth'):
            call_command('seed_legacy_synthetic')

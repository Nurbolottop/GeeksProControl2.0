"""Этап 10A: понедельничные снимки реально запускаются планировщиком."""
import datetime
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from apps.reports.models import WeeklyReport


class SnapshotScheduleTests(TestCase):
    def test_beat_schedule_points_to_registered_tasks(self):
        from core.celery import app

        app.loader.import_default_modules()
        registered = set(app.tasks)
        schedule = settings.CELERY_BEAT_SCHEDULE
        self.assertEqual(schedule['weekly-metrics']['task'], 'apps.reports.tasks.generate_weekly_metrics')
        self.assertEqual(schedule['weekly-metrics']['schedule'].day_of_week, {1})  # понедельник
        for item in schedule.values():
            self.assertIn(item['task'], registered)

    def test_missed_monday_is_caught_up_with_real_timestamp(self):
        from apps.notifications.tasks import ensure_weekly_snapshot

        self.assertTrue(ensure_weekly_snapshot())
        today = timezone.localdate()
        report = WeeklyReport.objects.get(week_start=today - datetime.timedelta(days=today.weekday() + 7))
        self.assertIn('dashboard_state', report.data)
        self.assertIn('dashboard_state_taken_at', report.data)  # видно, когда снят на самом деле
        self.assertFalse(ensure_weekly_snapshot())  # повторно не создаёт
        with mock.patch('apps.reports.services.generate_weekly_report') as generate:
            ensure_weekly_snapshot()
            generate.assert_not_called()

import datetime

from django.test import TestCase
from django.utils import timezone

from apps.notifications.models import Notification, NotificationLevel
from apps.notifications.services import notify
from apps.notifications.tasks import run_daily_checks
from apps.projects.models import Project


class NotifyServiceTests(TestCase):
    def test_dedup_key_prevents_duplicates(self):
        notify('A', dedup_key='x')
        notify('A', dedup_key='x')
        self.assertEqual(Notification.objects.count(), 1)

    def test_closed_notification_allows_new_one(self):
        first = notify('A', dedup_key='x')
        first.is_closed = True
        first.save()
        notify('A', dedup_key='x')
        self.assertEqual(Notification.objects.count(), 2)


class DailyChecksTests(TestCase):
    """Автоматические проверки (ТЗ §22)."""

    def test_overdue_project_creates_critical_notification(self):
        Project.objects.create(
            name='Просрочка',
            planned_end_date=timezone.localdate() - datetime.timedelta(days=2),
        )
        created = run_daily_checks()
        self.assertGreater(created, 0)
        notification = Notification.objects.filter(
            level=NotificationLevel.CRITICAL, title__contains='Просрочка',
        ).first()
        self.assertIsNotNone(notification)

    def test_checks_are_idempotent(self):
        Project.objects.create(
            name='Просрочка',
            planned_end_date=timezone.localdate() - datetime.timedelta(days=2),
        )
        run_daily_checks()
        count_after_first = Notification.objects.count()
        run_daily_checks()
        self.assertEqual(Notification.objects.count(), count_after_first)

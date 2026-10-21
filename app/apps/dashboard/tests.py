import datetime

from django.test import TestCase
from django.utils import timezone

from apps.dashboard.selectors import attention_items
from apps.interns.models import GraduateStatus, Intern
from apps.projects.models import Project, ProjectStatus
from apps.teams.models import TeamMember, TeamRole


class PendingGraduateReminderTests(TestCase):
    """«Требует внимания»: выпускник, зависший «на проверке» без решения
    дольше 2 недель — нужно напомнить, иначе про него просто забывают."""

    def _graduate(self, name, days_ago, status=GraduateStatus.PENDING):
        today = timezone.localdate()
        project = Project.objects.create(
            name=f'Проект {name}', status=ProjectStatus.COMPLETED,
            actual_end_date=today - datetime.timedelta(days=days_ago),
        )
        intern = Intern.objects.create(full_name=name, graduate_status=status)
        TeamMember.objects.create(
            project=project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.LEFT,
            left_at=today - datetime.timedelta(days=days_ago),
        )
        return intern

    def test_stale_pending_graduate_is_flagged(self):
        intern = self._graduate('Завис На Проверке', days_ago=20)
        texts = [item['text'] for item in attention_items()]
        self.assertTrue(any('Завис На Проверке' in text for text in texts))
        self.assertTrue(any(intern.get_absolute_url() == item['url'] for item in attention_items()))

    def test_recent_pending_graduate_not_flagged(self):
        self._graduate('Свежий Выпускник', days_ago=3)
        texts = [item['text'] for item in attention_items()]
        self.assertFalse(any('Свежий Выпускник' in text for text in texts))

    def test_declined_graduate_not_flagged(self):
        self._graduate('Отказался', days_ago=20, status=GraduateStatus.DECLINED)
        texts = [item['text'] for item in attention_items()]
        self.assertFalse(any('Отказался' in text for text in texts))

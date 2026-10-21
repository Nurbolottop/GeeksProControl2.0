from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.common.models import TimeStampedModel
from apps.projects.models import Project


class MeetingType(models.TextChoices):
    """Типы собраний (ТЗ §19)."""

    PM_WEEKLY = 'pm_weekly', 'PM Weekly'
    TEAM_LEAD = 'team_lead', 'Team Lead Meeting'
    PROJECT = 'project', 'Project Meeting'
    KICKOFF = 'kickoff', 'Kickoff'
    CLIENT = 'client', 'Client Meeting'
    DELIVERY = 'delivery', 'Delivery Meeting'
    INTERNAL = 'internal', 'Internal'
    OTHER = 'other', 'Other'


class Meeting(TimeStampedModel):
    """Собрание (ТЗ §19)."""

    topic = models.CharField('Тема', max_length=255)
    date = models.DateField('Дата', db_index=True)
    time = models.TimeField('Время', null=True, blank=True)
    meeting_type = models.CharField(
        'Тип', max_length=20,
        choices=MeetingType.choices, default=MeetingType.INTERNAL,
    )
    project = models.ForeignKey(
        Project, on_delete=models.SET_NULL, related_name='meetings',
        verbose_name='Проект', null=True, blank=True,
    )
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name='meetings',
        verbose_name='Участники', blank=True,
    )
    external_participants = models.CharField(
        'Внешние участники', max_length=255, blank=True,
        help_text='Например, представители клиента',
    )
    agenda = models.TextField('Повестка', blank=True)
    discussion = models.TextField('Обсуждение', blank=True)
    next_meeting_date = models.DateField(
        'Следующее собрание', null=True, blank=True,
    )

    class Meta:
        verbose_name = 'Собрание'
        verbose_name_plural = 'Собрания'
        ordering = ['-date', '-time']

    def __str__(self) -> str:
        return f'{self.topic} ({self.date:%d.%m.%Y})'

    def get_absolute_url(self) -> str:
        return reverse('meetings:detail', args=[self.pk])


class MeetingDecision(TimeStampedModel):
    """Решение собрания (ТЗ §19.2). Можно превратить в задачу."""

    class Status(models.TextChoices):
        OPEN = 'open', 'Открыто'
        DONE = 'done', 'Выполнено'
        CANCELLED = 'cancelled', 'Отменено'

    meeting = models.ForeignKey(
        Meeting, on_delete=models.CASCADE, related_name='decisions',
        verbose_name='Собрание',
    )
    text = models.TextField('Решение')
    responsible = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Ответственный', null=True, blank=True,
    )
    deadline = models.DateField('Deadline', null=True, blank=True)
    status = models.CharField(
        'Статус', max_length=10,
        choices=Status.choices, default=Status.OPEN,
    )
    task = models.ForeignKey(
        'tasks.Task', on_delete=models.SET_NULL, related_name='+',
        verbose_name='Задача', null=True, blank=True,
    )

    class Meta:
        verbose_name = 'Решение собрания'
        verbose_name_plural = 'Решения собраний'
        ordering = ['created_at']

    def __str__(self) -> str:
        return self.text[:80]

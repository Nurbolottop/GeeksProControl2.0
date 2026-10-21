from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel
from apps.projects.models import Project


class RiskCategory(models.TextChoices):
    """Категории рисков (ТЗ §21)."""

    DEADLINES = 'deadlines', 'Сроки'
    CLIENT = 'client', 'Клиент'
    DOCUMENTS = 'documents', 'Документы'
    TEAM = 'team', 'Команда'
    RESOURCES = 'resources', 'Ресурсы'
    TECHNICAL = 'technical', 'Технический'
    QA = 'qa', 'QA'
    REQUIREMENTS = 'requirements', 'Требования'
    OTHER = 'other', 'Другое'


class DelayReason(models.Model):
    """Справочник причин задержек (ТЗ §21)."""

    name = models.CharField('Причина', max_length=255, unique=True)
    is_active = models.BooleanField('Активна', default=True)

    class Meta:
        verbose_name = 'Причина задержки'
        verbose_name_plural = 'Причины задержек'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


DEFAULT_DELAY_REASONS = [
    'Клиент задержал материалы',
    'Изменение ТЗ',
    'Нехватка ресурсов',
    'Техническая проблема',
    'Слабая команда',
    'PM',
    'Team Lead',
    'Документы',
    'Инфраструктура',
    'Другое',
]


class Risk(TimeStampedModel):
    """Риск проекта — ручной или автоматический (ТЗ §21)."""

    class Status(models.TextChoices):
        OPEN = 'open', 'Открыт'
        CLOSED = 'closed', 'Закрыт'

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='risks',
        verbose_name='Проект',
    )
    category = models.CharField(
        'Категория', max_length=20,
        choices=RiskCategory.choices, default=RiskCategory.OTHER,
    )
    description = models.TextField('Описание')
    delay_reason = models.ForeignKey(
        DelayReason, on_delete=models.SET_NULL, related_name='risks',
        verbose_name='Причина задержки', null=True, blank=True,
    )
    is_auto = models.BooleanField('Автоматический', default=False)
    status = models.CharField(
        'Статус', max_length=10,
        choices=Status.choices, default=Status.OPEN, db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Создал', null=True, blank=True,
    )

    class Meta:
        verbose_name = 'Риск'
        verbose_name_plural = 'Риски'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.project.code}: {self.description[:60]}'

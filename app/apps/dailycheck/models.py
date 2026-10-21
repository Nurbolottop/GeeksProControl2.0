"""Раздел «Ежедневно» из карточки проекта убран — он оказался не нужен.

Модели оставлены, чтобы не потерять уже накопленные отметки: таблицы в
базе целы, но в интерфейсе раздела больше нет.
"""
from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class ProjectCheckItem(TimeStampedModel):
    """Ежедневный пункт по конкретному проекту.

    Набор у всех проектов одинаковый, но лишнее можно убрать,
    а своё — добавить.
    """

    project = models.ForeignKey(
        'projects.Project', on_delete=models.CASCADE,
        related_name='daily_items', verbose_name='Проект',
    )
    title = models.CharField('Пункт', max_length=200)
    hint = models.CharField('Пояснение', max_length=255, blank=True)
    order = models.PositiveSmallIntegerField('Порядок', default=100)
    is_active = models.BooleanField('Показывать', default=True, db_index=True)

    class Meta:
        verbose_name = 'Ежедневный пункт проекта'
        verbose_name_plural = 'Ежедневные пункты проектов'
        ordering = ['order', 'pk']

    def __str__(self) -> str:
        return f'{self.project.code}: {self.title}'


class ProjectCheckMark(TimeStampedModel):
    """Отметка по ежедневному пункту проекта за день."""

    date = models.DateField('Дата', db_index=True)
    item = models.ForeignKey(
        ProjectCheckItem, on_delete=models.CASCADE, related_name='marks',
        verbose_name='Пункт',
    )
    is_done = models.BooleanField('Проверено', default=True)
    note = models.CharField('Что заметил', max_length=255, blank=True)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Проверил', null=True, blank=True,
    )

    class Meta:
        verbose_name = 'Отметка по проекту'
        verbose_name_plural = 'Ежедневные отметки по проектам'
        unique_together = [('date', 'item')]
        ordering = ['-date']

    def __str__(self) -> str:
        return f'{self.date:%d.%m.%Y} — {self.item.title}'


# Стартовый набор пунктов для проекта. Пока пуст — состав согласуется.
PROJECT_DEFAULT_ITEMS: list[tuple[str, str]] = []


def ensure_project_items(project) -> None:
    """Разворачивает стандартный набор пунктов для проекта."""
    if ProjectCheckItem.objects.filter(project=project).exists():
        return
    ProjectCheckItem.objects.bulk_create([
        ProjectCheckItem(
            project=project, title=title, hint=hint, order=(index + 1) * 10,
        )
        for index, (title, hint) in enumerate(PROJECT_DEFAULT_ITEMS)
    ])

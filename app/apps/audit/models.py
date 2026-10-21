from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class AuditLog(TimeStampedModel):
    """Глобальный журнал действий (ТЗ §27).

    Обязательно логируются: изменения deadline, статуса, этапа, PM/TL,
    progress; удаление проекта/документа; закрытие и принудительное
    закрытие проекта.
    """

    object_type = models.CharField('Тип объекта', max_length=50, db_index=True)
    object_id = models.CharField('ID объекта', max_length=20, blank=True)
    object_repr = models.CharField('Объект', max_length=255)
    action = models.CharField('Действие', max_length=100)
    old_value = models.TextField('Старое значение', blank=True)
    new_value = models.TextField('Новое значение', blank=True)
    reason = models.TextField('Причина', blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Пользователь', null=True, blank=True,
    )

    class Meta:
        verbose_name = 'Запись аудита'
        verbose_name_plural = 'Журнал аудита'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.action}: {self.object_repr}'

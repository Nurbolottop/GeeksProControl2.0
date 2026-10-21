from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.common.models import TimeStampedModel


class Script(TimeStampedModel):
    """Заготовка текста: сообщение стажёру, письмо клиенту, объявление.

    Хранить и копировать — вся суть: сам текст никуда не отправляется,
    его вставляют руками в мессенджер или почту.
    """

    title = models.CharField('Название', max_length=255)
    category = models.CharField(
        'Категория', max_length=100, blank=True,
        help_text='Например: Стажёрам, Клиентам, Вакансии. Можно оставить пустым.',
    )
    body = models.TextField('Текст')
    is_pinned = models.BooleanField(
        'Закрепить сверху', default=False,
        help_text='Часто используемые — наверх списка.',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Автор', null=True, blank=True,
    )

    class Meta:
        verbose_name = 'Скрипт'
        verbose_name_plural = 'Скрипты'
        ordering = ['-is_pinned', '-updated_at']

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse('scripts:list')

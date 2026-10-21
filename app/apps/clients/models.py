from django.db import models

from apps.common.models import ArchivableModel, TimeStampedModel


class Client(TimeStampedModel, ArchivableModel):
    """Заказчик. Один клиент может иметь несколько проектов (ТЗ §16)."""

    organization = models.CharField('Организация', max_length=255)
    contact_name = models.CharField('ФИО представителя', max_length=255, blank=True)
    phone = models.CharField('Телефон', max_length=32, blank=True)
    email = models.EmailField('Email', blank=True)
    address = models.CharField('Адрес', max_length=255, blank=True)
    city = models.CharField('Город', max_length=100, blank=True, db_index=True)
    requisites = models.TextField('Реквизиты', blank=True)
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        verbose_name = 'Клиент'
        verbose_name_plural = 'Клиенты'
        ordering = ['organization']

    def __str__(self) -> str:
        return self.organization


class ClientContact(TimeStampedModel):
    """Дополнительное контактное лицо клиента."""

    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name='contacts',
        verbose_name='Клиент',
    )
    name = models.CharField('ФИО', max_length=255)
    position = models.CharField('Должность', max_length=100, blank=True)
    phone = models.CharField('Телефон', max_length=32, blank=True)
    email = models.EmailField('Email', blank=True)
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        verbose_name = 'Контакт клиента'
        verbose_name_plural = 'Контакты клиентов'

    def __str__(self) -> str:
        return f'{self.name} ({self.client})'

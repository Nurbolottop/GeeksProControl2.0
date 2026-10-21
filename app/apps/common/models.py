from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    """Абстрактная модель с датами создания и изменения."""

    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Изменено', auto_now=True)

    class Meta:
        abstract = True


class ArchivableQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_archived=False)

    def archived(self):
        return self.filter(is_archived=True)


class ArchivableModel(models.Model):
    """Абстрактная модель с soft delete / архивированием (ТЗ §32).

    Критические сущности (проекты, документы, стажёры, задачи)
    не удаляются физически, а помечаются архивными.
    """

    is_archived = models.BooleanField('В архиве', default=False)
    archived_at = models.DateTimeField('Дата архивирования', null=True, blank=True)

    objects = ArchivableQuerySet.as_manager()

    class Meta:
        abstract = True

    def archive(self):
        self.is_archived = True
        self.archived_at = timezone.now()
        self.save(update_fields=['is_archived', 'archived_at'])

    def unarchive(self):
        self.is_archived = False
        self.archived_at = None
        self.save(update_fields=['is_archived', 'archived_at'])

from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.common.models import TimeStampedModel
from apps.projects.models import Project, ProjectType
from apps.training.models import Specialization


class PlannedProject(TimeStampedModel):
    """Планируемый (ещё не стартовавший) проект (ТЗ §15).

    Участвует в Resource Planning: потребность в людях считается
    из его состава команды.
    """

    class Status(models.TextChoices):
        POTENTIAL = 'potential', 'Потенциальный'
        NEGOTIATION = 'negotiation', 'Переговоры'
        AWAITING_DOCS = 'awaiting_docs', 'Ожидаем документы'
        CONFIRMED = 'confirmed', 'Подтверждён'
        TEAM_FORMING = 'team_forming', 'Формирование команды'
        LAUNCHED = 'launched', 'Запущен'
        REJECTED = 'rejected', 'Отказ'

    # Статусы, при которых потребность учитывается в прогнозе
    ACTIVE_STATUSES = (
        Status.NEGOTIATION, Status.AWAITING_DOCS,
        Status.CONFIRMED, Status.TEAM_FORMING,
    )

    name = models.CharField('Название', max_length=255)
    status = models.CharField(
        'Статус', max_length=20,
        choices=Status.choices, default=Status.POTENTIAL, db_index=True,
    )
    probability = models.PositiveSmallIntegerField(
        'Вероятность запуска, %', default=50,
    )
    expected_start = models.DateField(
        'Ожидаемая дата старта', null=True, blank=True,
    )
    project_type = models.ForeignKey(
        ProjectType, on_delete=models.SET_NULL, related_name='+',
        verbose_name='Тип проекта', null=True, blank=True,
    )
    duration_months = models.PositiveSmallIntegerField(
        'Длительность, мес.', null=True, blank=True,
    )
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        verbose_name = 'Планируемый проект'
        verbose_name_plural = 'Планируемые проекты'
        ordering = ['expected_start']

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse('resources:planned_update', args=[self.pk])


class PlannedProjectNeed(models.Model):
    """Предполагаемый состав команды планируемого проекта."""

    planned_project = models.ForeignKey(
        PlannedProject, on_delete=models.CASCADE, related_name='needs',
        verbose_name='Планируемый проект',
    )
    specialization = models.ForeignKey(
        Specialization, on_delete=models.CASCADE, related_name='+',
        verbose_name='Направление',
    )
    count = models.PositiveSmallIntegerField('Человек', default=1)

    class Meta:
        verbose_name = 'Потребность планируемого проекта'
        verbose_name_plural = 'Потребности планируемых проектов'
        unique_together = [('planned_project', 'specialization')]

    def __str__(self) -> str:
        return f'{self.planned_project}: {self.specialization} × {self.count}'


class StaffingRequest(TimeStampedModel):
    """Запрос на стажёров под конкретный (уже существующий) проект.

    В отличие от PlannedProjectNeed — не про гипотетический будущий
    проект, а про нехватку людей на реальном текущем.
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='staffing_requests',
        verbose_name='Проект',
    )
    specialization = models.ForeignKey(
        Specialization, on_delete=models.PROTECT, related_name='+',
        verbose_name='Направление',
    )
    count = models.PositiveSmallIntegerField('Сколько человек', default=1)
    needed_by = models.DateField('Нужны к дате', null=True, blank=True)
    comment = models.TextField('Комментарий', blank=True)
    is_closed = models.BooleanField('Закрыт', default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+', verbose_name='Кто запросил',
    )

    class Meta:
        verbose_name = 'Запрос на стажёров'
        verbose_name_plural = 'Запросы на стажёров'
        ordering = ['is_closed', 'needed_by']

    def __str__(self) -> str:
        return f'{self.project}: {self.specialization} × {self.count}'

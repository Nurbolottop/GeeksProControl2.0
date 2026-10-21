from django.db import models
from django.urls import reverse

from apps.common.models import TimeStampedModel


class Flow(TimeStampedModel):
    """Поток — набор стажёров, который вместе ведёт свои проекты.

    Главная сущность GeeksPro: под поток набираются проекты и команда,
    поток отрабатывает несколько месяцев и закрывается.
    """

    class Status(models.TextChoices):
        PLANNED = 'planned', 'Набор'
        ACTIVE = 'active', 'Активный'
        FINISHED = 'finished', 'Завершён'

    number = models.PositiveSmallIntegerField('Номер потока', unique=True)
    status = models.CharField(
        'Статус', max_length=10,
        choices=Status.choices, default=Status.ACTIVE, db_index=True,
    )
    start_date = models.DateField('Начало стажировки', null=True, blank=True)
    end_date = models.DateField('Окончание стажировки', null=True, blank=True)
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        verbose_name = 'Поток'
        verbose_name_plural = 'Потоки'
        ordering = ['-number']

    def __str__(self) -> str:
        return f'Поток {self.number}'

    def get_absolute_url(self) -> str:
        return reverse('flows:detail', args=[self.pk])


class Group(TimeStampedModel):
    """Группа потока — она же команда: один состав ведёт один проект.

    Нумерация внутри потока: группа 1 потока 13 работает над проектом 13.1.
    """

    flow = models.ForeignKey(
        Flow, on_delete=models.CASCADE, related_name='groups',
        verbose_name='Поток',
    )
    number = models.PositiveSmallIntegerField('Номер группы')
    name = models.CharField('Название', max_length=100, blank=True)
    project = models.OneToOneField(
        'projects.Project', on_delete=models.SET_NULL, related_name='group',
        verbose_name='Проект', null=True, blank=True,
    )
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        verbose_name = 'Группа'
        verbose_name_plural = 'Группы'
        ordering = ['flow__number', 'number']
        unique_together = [('flow', 'number')]

    def __str__(self) -> str:
        return f'Группа {self.code}'

    @property
    def code(self) -> str:
        return f'{self.flow.number}.{self.number}'

    def get_absolute_url(self) -> str:
        return reverse('flows:group_detail', args=[self.pk])

    @property
    def active_members(self):
        return self.members.filter(status='active')

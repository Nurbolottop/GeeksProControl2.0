from django.db import models
from django.urls import reverse

from apps.common.models import TimeStampedModel


class Specialization(models.Model):
    """Направление (ТЗ §12): Backend, Frontend, UX/UI, Mobile, QA, PM."""

    name = models.CharField('Название', max_length=100, unique=True)

    class Meta:
        verbose_name = 'Направление'
        verbose_name_plural = 'Направления'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


# Те же значения, что у филиала стажёра — чтобы данные сходились
BRANCHES = [('Бишкек', 'Бишкек'), ('Ош', 'Ош')]


class GroupStatus(models.TextChoices):
    """Где группа академии находится сейчас."""

    RECRUITING = 'recruiting', 'Набор'
    STUDYING = 'studying', 'Учится'
    GRADUATED = 'graduated', 'Выпущена'
    CANCELLED = 'cancelled', 'Не состоялась'


class TrainingGroup(TimeStampedModel):
    """Учебная группа IT-академии (ТЗ §13).

    Из этих групп приходят стажёры, поэтому по каждой держим три числа:
    сколько учится, сколько сказали, что хотят на стажировку, и сколько
    в итоге дошло. На них строится план-график набора по месяцам.
    """

    number = models.CharField('Номер группы', max_length=50)
    specialization = models.ForeignKey(
        Specialization, on_delete=models.PROTECT, related_name='groups',
        verbose_name='Направление',
    )
    branch = models.CharField(
        'Филиал', max_length=100, blank=True, choices=BRANCHES, db_index=True,
        help_text='Бишкек и Ош набирают группы независимо, номера могут совпадать.',
    )
    start_date = models.DateField('Дата начала', null=True, blank=True)
    end_date = models.DateField('Дата окончания', null=True, blank=True, db_index=True)
    status = models.CharField(
        'Статус', max_length=12, choices=GroupStatus.choices,
        default=GroupStatus.STUDYING, db_index=True,
        help_text='Ставится сам по датам; вручную — только «Не состоялась».',
    )
    students_count = models.PositiveSmallIntegerField(
        'Обучающихся', null=True, blank=True,
        help_text='Пусто — академия ещё не сообщила.',
    )
    students_note = models.CharField(
        'Уточнение по студентам', max_length=100, blank=True,
        help_text='Как прислали: «5–7», «на старте» и т.п.',
    )
    wants_internship = models.PositiveSmallIntegerField(
        'Хотят на стажировку', default=0,
        help_text='Сколько студентов сами сказали, что пойдут на стажировку.',
    )
    expected_interns = models.PositiveSmallIntegerField(
        'Прогноз перехода в стажировку', default=0,
        help_text='Наша оценка: сколько реально дойдёт.',
    )
    actual_interns = models.PositiveSmallIntegerField(
        'Фактически пришло', default=0,
    )
    teacher = models.CharField('Преподаватель', max_length=255, blank=True)
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        verbose_name = 'Учебная группа'
        verbose_name_plural = 'Учебные группы'
        ordering = ['end_date']

    def __str__(self) -> str:
        return f'{self.number} ({self.specialization})'

    def get_absolute_url(self) -> str:
        return reverse('training:group_list')

    def save(self, *args, **kwargs):
        # Академия присылает только даты, поэтому стадию группы не ведём
        # руками, а выводим из них. «Не состоялась» — единственное, что
        # нельзя узнать по датам, его и оставляем ручным.
        if self.status != GroupStatus.CANCELLED:
            self.status = self.stage_by_dates()
        super().save(*args, **kwargs)

    def stage_by_dates(self, today=None) -> str:
        from django.utils import timezone

        today = today or timezone.localdate()
        if self.start_date and today < self.start_date:
            return GroupStatus.RECRUITING
        if self.end_date and today > self.end_date:
            return GroupStatus.GRADUATED
        return GroupStatus.STUDYING

    @property
    def stage(self) -> str:
        """Стадия на сегодня: по датам, а не по сохранённому полю.

        Сохранённый статус устаревает — группа, заведённая «учится»,
        через полгода уже выпущена, хотя её никто не пересохранял.
        """
        if self.status == GroupStatus.CANCELLED:
            return GroupStatus.CANCELLED
        return self.stage_by_dates()

    @property
    def stage_label(self) -> str:
        return GroupStatus(self.stage).label

    @property
    def is_open(self) -> bool:
        """Группа ещё в работе — её выпуск попадает в план-график."""
        return self.stage in (GroupStatus.RECRUITING, GroupStatus.STUDYING)

    @property
    def conversion(self):
        """Доля дошедших до стажировки — когда группа выпущена и факт внесён."""
        if self.stage != GroupStatus.GRADUATED or not self.students_count:
            return None
        if not self.actual_interns:
            return None
        return round(self.actual_interns / self.students_count * 100)

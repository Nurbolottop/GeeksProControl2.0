import datetime

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class WeeklyReport(TimeStampedModel):
    """Недельный отчёт (ТЗ §24.1). Показатели фиксируются в JSON."""

    week_start = models.DateField('Начало недели', unique=True)
    data = models.JSONField('Показатели', default=dict)

    comment = models.TextField('Комментарий руководителя', blank=True)

    class Meta:
        verbose_name = 'Недельный отчёт'
        verbose_name_plural = 'Недельные отчёты'
        ordering = ['-week_start']

    def __str__(self) -> str:
        return f'Неделя с {self.week_start:%d.%m.%Y}'


class MonthlyReport(TimeStampedModel):
    """Месячный отчёт по форме рабочей таблицы. Показатели — в JSON."""

    month_start = models.DateField('Месяц', unique=True)
    data = models.JSONField('Показатели', default=dict)
    comment = models.TextField('Комментарий руководителя', blank=True)

    class Meta:
        verbose_name = 'Месячный отчёт'
        verbose_name_plural = 'Месячные отчёты'
        ordering = ['-month_start']

    def __str__(self) -> str:
        return f'Отчёт за {self.month_start:%m.%Y}'


class KPISnapshot(TimeStampedModel):
    """Снимок KPI за период (ТЗ §25)."""

    class Period(models.TextChoices):
        WEEK = 'week', 'Неделя'
        MONTH = 'month', 'Месяц'

    period_type = models.CharField(
        'Период', max_length=10, choices=Period.choices,
    )
    period_start = models.DateField('Начало периода')
    data = models.JSONField('KPI', default=dict)

    class Meta:
        verbose_name = 'KPI snapshot'
        verbose_name_plural = 'KPI snapshots'
        unique_together = [('period_type', 'period_start')]
        ordering = ['-period_start']

    def __str__(self) -> str:
        return f'{self.get_period_type_display()} с {self.period_start:%d.%m.%Y}'


class WrittenNote(TimeStampedModel):
    """Запись руководителя: достижение, проблема или вопрос.

    Добавляется по одной, дата ставится днём написания.
    """

    class Kind(models.TextChoices):
        ACHIEVEMENT = 'achievement', 'Достижение'
        PROBLEM = 'problem', 'Проблема'
        QUESTION = 'question', 'Вопрос'

    kind = models.CharField(
        'Раздел', max_length=20,
        choices=Kind.choices, default=Kind.ACHIEVEMENT, db_index=True,
    )
    text = models.TextField('Текст')
    date = models.DateField('Дата', auto_now_add=True, db_index=True)
    week_start = models.DateField('Неделя', db_index=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Автор', null=True, blank=True,
    )

    class Meta:
        verbose_name = 'Запись отчёта'
        verbose_name_plural = 'Записи отчёта'
        ordering = ['-week_start', '-created_at']

    def __str__(self) -> str:
        return f'{self.get_kind_display()} за неделю {self.week_start:%d.%m.%Y}'

    def save(self, *args, **kwargs):
        # Запись относится к неделе, в которую её написали
        if not self.week_start:
            from django.utils import timezone

            day = self.date or timezone.localdate()
            self.week_start = day - datetime.timedelta(days=day.weekday())
        super().save(*args, **kwargs)

    @property
    def week_end(self) -> datetime.date:
        return self.week_start + datetime.timedelta(days=6)


# ===================================================== этап 7B: еженедельная отчётность
# Неделя — понедельник–воскресенье по времени Бишкека (TIME_ZONE); week_start —
# понедельник. Даты подачи (created_at) — фактические, не подменяются.

class LeadWeeklyReport(TimeStampedModel):
    """Отчёт тимлида по закреплённому проекту и направлению.

    Обязательный (weekly) — один на тимлида, проект и направление за неделю
    (ограничение в БД); дополнительные (extra) — по запросу, без ограничения
    и в счёт обязательного не идут. Сохраняется сразу, без утверждения."""

    class Kind(models.TextChoices):
        WEEKLY = 'weekly', 'Еженедельный'
        EXTRA = 'extra', 'Дополнительный'

    lead = models.ForeignKey('interns.Intern', on_delete=models.PROTECT, related_name='lead_reports',
                             verbose_name='Тимлид')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='+', verbose_name='Учётная запись автора')
    project = models.ForeignKey('projects.Project', on_delete=models.PROTECT, related_name='lead_reports',
                                verbose_name='Проект')
    direction = models.CharField('Направление', max_length=20, blank=True)
    week_start = models.DateField('Неделя (понедельник)', db_index=True)
    kind = models.CharField('Вид', max_length=10, choices=Kind.choices, default=Kind.WEEKLY)
    text = models.TextField('Отчёт')

    class Meta:
        verbose_name = 'Отчёт тимлида'
        verbose_name_plural = 'Отчёты тимлидов'
        ordering = ['-week_start', '-created_at']
        constraints = [models.UniqueConstraint(
            fields=['lead', 'project', 'direction', 'week_start'], condition=models.Q(kind='weekly'),
            name='lead_report_one_weekly')]

    def __str__(self) -> str:
        return f'{self.lead} — {self.project}, неделя {self.week_start:%d.%m.%Y}'


class PmWeeklyReport(TimeStampedModel):
    """Сводный еженедельный отчёт ПМ руководителю — один на ПМ за неделю.

    Срок — пятница или суббота отчётной недели: on_time фиксируется по
    фактической дате подачи и потом не меняется."""

    pm = models.ForeignKey('interns.Intern', on_delete=models.PROTECT, related_name='pm_weekly_reports',
                           verbose_name='ПМ')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='+', verbose_name='Учётная запись автора')
    week_start = models.DateField('Неделя (понедельник)', db_index=True)
    text = models.TextField('Отчёт')
    projects = models.ManyToManyField('projects.Project', blank=True, related_name='pm_weekly_reports',
                                      verbose_name='Проекты')
    submitted_on = models.DateField('Дата подачи')
    on_time = models.BooleanField('В срок (пт/сб)', default=False)

    class Meta:
        verbose_name = 'Отчёт ПМ'
        verbose_name_plural = 'Отчёты ПМ'
        ordering = ['-week_start', '-created_at']
        constraints = [models.UniqueConstraint(fields=['pm', 'week_start'], name='pm_report_one_per_week')]

    def __str__(self) -> str:
        return f'{self.pm} — неделя {self.week_start:%d.%m.%Y}'


class WeeklyEvaluation(TimeStampedModel):
    """Еженедельная оценка стажёра тимлидом: 1–10 и комментарий.

    Отдельно по каждому проекту (выпускник может быть в двух). Автор — тот
    тимлид, который ставил оценку (не текущий тимлид проекта). Старые
    оценки по семи критериям (interns.InternEvaluation) — отдельный архив,
    в эту шкалу не переводятся."""

    MIN, MAX = 1, 10

    intern = models.ForeignKey('interns.Intern', on_delete=models.PROTECT, related_name='weekly_evaluations',
                               verbose_name='Стажёр')
    project = models.ForeignKey('projects.Project', on_delete=models.PROTECT, related_name='weekly_evaluations',
                                verbose_name='Проект')
    direction = models.CharField('Направление', max_length=20, blank=True)
    week_start = models.DateField('Неделя (понедельник)', db_index=True)
    lead = models.ForeignKey('interns.Intern', on_delete=models.PROTECT, related_name='given_evaluations',
                             verbose_name='Тимлид')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='+', verbose_name='Учётная запись автора')
    score = models.PositiveSmallIntegerField('Оценка 1–10')
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        verbose_name = 'Еженедельная оценка'
        verbose_name_plural = 'Еженедельные оценки'
        ordering = ['-week_start', 'intern__full_name']
        constraints = [
            models.UniqueConstraint(fields=['intern', 'project', 'week_start'], name='weekly_evaluation_unique'),
            models.CheckConstraint(condition=models.Q(score__gte=1, score__lte=10), name='weekly_evaluation_scale'),
        ]

    def __str__(self) -> str:
        return f'{self.intern} — {self.project}, {self.week_start:%d.%m.%Y}: {self.score}/10'

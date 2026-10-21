import datetime

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.common.models import TimeStampedModel


class MeetingKind(models.TextChoices):
    """Виды регулярных собраний GeeksPro."""

    PM_INTERNS = 'pm_interns', 'PM со стажёрами'
    LEAD_INTERNS = 'lead_interns', 'Тимлид со стажёрами'
    INTERNAL = 'internal', 'Внутреннее (PM и тимлиды)'
    OTHER = 'other', 'Другое'


WEEKDAYS = [
    (0, 'Пн'), (1, 'Вт'), (2, 'Ср'), (3, 'Чт'),
    (4, 'Пт'), (5, 'Сб'), (6, 'Вс'),
]


class GroupMeeting(TimeStampedModel):
    """Собрание группы: конкретная дата, по которой ведётся табель.

    Собрание принадлежит направлению: тимлид дизайна собирает дизайнеров,
    бэкенда — бэкендеров. В одной команде в один день может быть несколько
    собраний разных направлений, и каждый тимлид отмечает только своих.
    Пустое ``direction`` — общее собрание команды (так заведены старые).
    """

    class Status(models.TextChoices):
        PLANNED = 'planned', 'Запланировано'
        HELD = 'held', 'Проведено'
        MISSED = 'missed', 'Не проведено'

    group = models.ForeignKey(
        'flows.Group', on_delete=models.CASCADE, related_name='meetings',
        verbose_name='Группа',
    )
    kind = models.CharField(
        'Вид', max_length=20,
        choices=MeetingKind.choices, default=MeetingKind.PM_INTERNS,
    )
    direction = models.CharField(
        'Направление', max_length=20, blank=True, db_index=True,
        help_text=(
            'Роль в команде, для которой собрание: тимлид собирает только '
            'своё направление. Пусто — общее собрание команды.'
        ),
    )
    date = models.DateField('Дата', db_index=True)
    host = models.ForeignKey(
        'interns.Intern', on_delete=models.SET_NULL, related_name='hosted_meetings',
        verbose_name='Провёл', null=True, blank=True,
    )
    status = models.CharField(
        'Статус', max_length=10,
        choices=Status.choices, default=Status.PLANNED, db_index=True,
    )
    topic = models.CharField('Тема', max_length=255, blank=True)
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        verbose_name = 'Собрание группы'
        verbose_name_plural = 'Собрания групп'
        ordering = ['date']
        unique_together = [('group', 'kind', 'date', 'direction')]
        indexes = [models.Index(fields=['group', 'date'])]

    def __str__(self) -> str:
        return f'{self.get_kind_display()} {self.date:%d.%m.%Y}'

    @property
    def direction_label(self) -> str:
        """«Дизайн», «Backend» — для подписи собрания в табеле."""
        from apps.teams.models import TeamRole

        if not self.direction:
            return 'Вся команда'
        return dict(TeamRole.choices).get(self.direction, self.direction)

    @property
    def short_kind(self) -> str:
        return {
            MeetingKind.PM_INTERNS: 'PM',
            MeetingKind.LEAD_INTERNS: 'ТЛ',
            MeetingKind.INTERNAL: 'Вн',
        }.get(self.kind, '—')

    @property
    def attendance_rate(self):
        marks = list(self.attendance.all())
        if not marks:
            return None
        attended = sum(1 for mark in marks if mark.is_attended)
        return round(attended / len(marks) * 100)

    @property
    def previous(self):
        """Предыдущее собрание того же вида в этой команде."""
        return (
            GroupMeeting.objects
            .filter(group_id=self.group_id, kind=self.kind, date__lt=self.date)
            .order_by('-date')
            .first()
        )

    @property
    def period_start(self) -> datetime.date | None:
        """С какой даты оцениваем работу — с прошлого собрания."""
        previous = self.previous
        return previous.date if previous else None

    @property
    def period_days(self) -> int | None:
        start = self.period_start
        return (self.date - start).days if start else None

    @property
    def average_score(self):
        scores = [item.score for item in self.scores.all()]
        return round(sum(scores) / len(scores), 1) if scores else None


class Attendance(TimeStampedModel):
    """Отметка посещения конкретного собрания."""

    class Status(models.TextChoices):
        PRESENT = 'present', 'Был'
        ABSENT = 'absent', 'Не был'
        LATE = 'late', 'Опоздал'
        EXCUSED = 'excused', 'Уважительная'

    # Порядок переключения по клику в табеле
    CYCLE = [Status.PRESENT, Status.ABSENT, Status.LATE, Status.EXCUSED]

    meeting = models.ForeignKey(
        GroupMeeting, on_delete=models.CASCADE, related_name='attendance',
        verbose_name='Собрание',
    )
    intern = models.ForeignKey(
        'interns.Intern', on_delete=models.CASCADE, related_name='attendance',
        verbose_name='Человек',
    )
    status = models.CharField(
        'Отметка', max_length=10, choices=Status.choices,
        default=Status.PRESENT,
    )
    comment = models.CharField('Комментарий', max_length=255, blank=True)
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Отметил', null=True, blank=True,
    )

    class Meta:
        verbose_name = 'Отметка посещения'
        verbose_name_plural = 'Табель посещаемости'
        unique_together = [('meeting', 'intern')]

    def __str__(self) -> str:
        return f'{self.intern} — {self.meeting}: {self.get_status_display()}'

    @property
    def is_attended(self) -> bool:
        return self.status in (self.Status.PRESENT, self.Status.LATE)


class WorkScore(TimeStampedModel):
    """Оценка работы человека за период между собраниями: от 0 до 10."""

    MAX = 10
    SCALE = list(range(MAX + 1))

    meeting = models.ForeignKey(
        GroupMeeting, on_delete=models.CASCADE, related_name='scores',
        verbose_name='Собрание',
    )
    intern = models.ForeignKey(
        'interns.Intern', on_delete=models.CASCADE, related_name='work_scores',
        verbose_name='Человек',
    )
    score = models.PositiveSmallIntegerField(
        'Балл', validators=[MinValueValidator(0), MaxValueValidator(MAX)],
    )
    comment = models.CharField('Комментарий', max_length=255, blank=True)
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Оценил', null=True, blank=True,
    )

    class Meta:
        verbose_name = 'Оценка работы'
        verbose_name_plural = 'Оценки работы'
        unique_together = [('meeting', 'intern')]

    def __str__(self) -> str:
        return f'{self.intern} — {self.meeting}: {self.score}/{self.MAX}'

    @property
    def level(self) -> str:
        """Для подсветки: high / mid / low."""
        if self.score >= 8:
            return 'high'
        return 'mid' if self.score >= 5 else 'low'

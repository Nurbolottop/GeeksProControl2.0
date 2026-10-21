from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.common.models import ArchivableModel, TimeStampedModel
from apps.projects.models import Project, ProjectStage


class TaskStatus(models.TextChoices):
    """Четыре состояния доски (этап 7A). Хранимые значения прежние —
    меняются только подписи: «Новая» = «К выполнению», «Выполнена» = «Готово».
    «Отменена» — архивное значение старых задач, на доске не показывается."""

    NEW = 'new', 'К выполнению'
    IN_PROGRESS = 'in_progress', 'В работе'
    REVIEW = 'review', 'На проверке'
    DONE = 'done', 'Готово'
    CANCELLED = 'cancelled', 'Отменена (архив)'


BOARD_STATUSES = (TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.DONE)

# Направление задачи — роли участников команды (без ПМ и тимлида)
DIRECTION_CHOICES = [
    ('backend', 'Backend'), ('frontend', 'Frontend'), ('mobile', 'Mobile'),
    ('uxui', 'UX/UI'), ('qa', 'QA'), ('other', 'Other'),
]


class TaskPriority(models.TextChoices):
    LOW = 'low', 'Low'
    MEDIUM = 'medium', 'Medium'
    HIGH = 'high', 'High'
    CRITICAL = 'critical', 'Critical'


class Task(TimeStampedModel, ArchivableModel):
    """Задача (ТЗ §10). Может относиться к проекту, этапу или быть личной."""

    title = models.CharField('Название', max_length=255)
    description = models.TextField('Описание', blank=True)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='tasks',
        verbose_name='Проект', null=True, blank=True,
    )
    stage = models.ForeignKey(
        ProjectStage, on_delete=models.SET_NULL, related_name='tasks',
        verbose_name='Этап', null=True, blank=True,
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='assigned_tasks', verbose_name='Исполнитель',
        null=True, blank=True,
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='authored_tasks', verbose_name='Автор',
        null=True, blank=True,
    )
    priority = models.CharField(
        'Приоритет', max_length=20,
        choices=TaskPriority.choices, default=TaskPriority.MEDIUM, db_index=True,
    )
    status = models.CharField(
        'Статус', max_length=20,
        choices=TaskStatus.choices, default=TaskStatus.NEW, db_index=True,
    )
    deadline = models.DateField('Deadline', null=True, blank=True, db_index=True)
    completed_at = models.DateField('Дата завершения', null=True, blank=True)
    direction = models.CharField('Направление', max_length=20, choices=DIRECTION_CHOICES, blank=True,
                                 db_index=True)
    needs_reassign = models.BooleanField(
        'Нужен новый исполнитель', default=False,
        help_text='Исполнитель вышел из проекта, не сдав работу: задача в очереди до нового назначения.',
    )

    class Meta:
        verbose_name = 'Задача'
        verbose_name_plural = 'Задачи'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'deadline'])]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse('tasks:detail', args=[self.pk])

    @property
    def is_open(self) -> bool:
        return self.status not in (TaskStatus.DONE, TaskStatus.CANCELLED)


class TaskAssignment(TimeStampedModel):
    """Исполнитель задачи — участник проекта (стажёр). У каждого свой
    результат: общий статус задачи выводится из всех активных назначений.

    Назначение не удаляется: при выходе из проекта незавершённое
    назначение закрывается (is_active=False), принятое остаётся."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='assignments', verbose_name='Задача')
    intern = models.ForeignKey('interns.Intern', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='task_assignments', verbose_name='Исполнитель')
    person_name = models.CharField('ФИО исполнителя', max_length=255)
    role = models.CharField('Роль в проекте', max_length=20, blank=True)
    status = models.CharField('Результат исполнителя', max_length=20, choices=TaskStatus.choices,
                              default=TaskStatus.NEW, db_index=True)
    is_active = models.BooleanField('Назначение действует', default=True, db_index=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='Назначил')
    released_at = models.DateTimeField('Назначение закрыто', null=True, blank=True)
    release_reason = models.CharField('Причина закрытия', max_length=255, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='Принял')
    approved_at = models.DateTimeField('Принято', null=True, blank=True)

    class Meta:
        verbose_name = 'Исполнитель задачи'
        verbose_name_plural = 'Исполнители задач'
        ordering = ['created_at', 'pk']
        constraints = [models.UniqueConstraint(fields=['task', 'intern'], condition=models.Q(is_active=True),
                                               name='task_assignment_one_active')]

    def __str__(self) -> str:
        return f'{self.person_name} — {self.task}'


class TaskSubmission(TimeStampedModel):
    """Сдача работы исполнителем (через Telegram-бота; этап 8 подключит
    кнопки). Скриншот обязателен. Каждая попытка — отдельная запись:
    материалы прежних попыток не перезаписываются."""

    class Outcome(models.TextChoices):
        PENDING = 'pending', 'На проверке'
        APPROVED = 'approved', 'Принято'
        RETURNED = 'returned', 'Возвращено на доработку'
        WITHDRAWN = 'withdrawn', 'Снято: исполнитель вышел из проекта'

    class Source(models.TextChoices):
        TELEGRAM = 'telegram', 'Telegram-бот'
        WEB = 'web', 'Control'

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='submissions', verbose_name='Задача')
    assignment = models.ForeignKey(TaskAssignment, on_delete=models.PROTECT, related_name='submissions',
                                   verbose_name='Исполнитель')
    attempt = models.PositiveSmallIntegerField('Попытка', default=1)
    screenshot = models.FileField('Скриншот результата', upload_to='tasks/submissions/%Y/%m/')
    links = models.TextField('Ссылки', blank=True)
    comment = models.TextField('Комментарий исполнителя', blank=True)
    source = models.CharField('Источник', max_length=20, choices=Source.choices, default=Source.TELEGRAM)
    outcome = models.CharField('Решение', max_length=20, choices=Outcome.choices, default=Outcome.PENDING,
                               db_index=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+', verbose_name='Проверил')
    reviewed_at = models.DateTimeField('Проверено', null=True, blank=True)
    review_comment = models.TextField('Комментарий проверки', blank=True)

    class Meta:
        verbose_name = 'Сдача работы'
        verbose_name_plural = 'Сдачи работ'
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['assignment'], condition=models.Q(outcome='pending'),
                                               name='task_submission_one_pending')]

    def __str__(self) -> str:
        return f'{self.assignment.person_name}: «{self.task}», попытка {self.attempt}'


class TaskEvent(models.Model):
    """История задачи: создание, назначения, статусы, сдачи, проверки,
    комментарии, архив. Только добавляется."""

    class Kind(models.TextChoices):
        CREATED = 'created', 'Создана'
        EDITED = 'edited', 'Изменена'
        ASSIGNED = 'assigned', 'Назначен исполнитель'
        UNASSIGNED = 'unassigned', 'Исполнитель снят'
        RELEASED = 'released', 'Назначение закрыто (выход из проекта)'
        STATUS = 'status', 'Статус'
        SUBMITTED = 'submitted', 'Работа отправлена'
        APPROVED = 'approved', 'Работа принята'
        RETURNED = 'returned', 'Возвращена на доработку'
        COMMENT = 'comment', 'Комментарий'
        ATTACHMENT = 'attachment', 'Материал'
        ARCHIVED = 'archived', 'В архиве'

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='events', verbose_name='Задача')
    kind = models.CharField('Событие', max_length=20, choices=Kind.choices)
    assignment = models.ForeignKey(TaskAssignment, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='Исполнитель')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='+', verbose_name='Кто')
    old_value = models.CharField('Было', max_length=255, blank=True)
    new_value = models.CharField('Стало', max_length=255, blank=True)
    text = models.TextField('Подробности', blank=True)
    created_at = models.DateTimeField('Когда', auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Событие задачи'
        verbose_name_plural = 'История задач'
        ordering = ['-created_at', '-pk']

    def __str__(self) -> str:
        return f'{self.get_kind_display()}: {self.task}'


class TaskComment(TimeStampedModel):
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name='comments',
        verbose_name='Задача',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Автор', null=True,
    )
    text = models.TextField('Комментарий')

    class Meta:
        verbose_name = 'Комментарий задачи'
        verbose_name_plural = 'Комментарии задач'
        ordering = ['created_at']

    def __str__(self) -> str:
        return f'Комментарий к «{self.task}»'


class TaskAttachment(TimeStampedModel):
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name='attachments',
        verbose_name='Задача',
    )
    file = models.FileField('Файл', upload_to='tasks/%Y/%m/')
    name = models.CharField('Название', max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Загрузил', null=True,
    )

    class Meta:
        verbose_name = 'Вложение задачи'
        verbose_name_plural = 'Вложения задач'

    def __str__(self) -> str:
        return self.name or self.file.name


class TaskTemplate(models.Model):
    """Шаблон автоматической задачи (ТЗ §10.1). Настраивается через админку."""

    class Kind(models.TextChoices):
        PROJECT_NEW = 'project_new', 'Новый проект'
        DELIVERY = 'delivery', 'Сдача проекта'

    kind = models.CharField('Событие', max_length=20, choices=Kind.choices)
    title = models.CharField('Название задачи', max_length=255)
    order = models.PositiveSmallIntegerField('Порядок', default=0)
    is_active = models.BooleanField('Активен', default=True)

    class Meta:
        verbose_name = 'Шаблон задачи'
        verbose_name_plural = 'Шаблоны задач'
        ordering = ['kind', 'order']

    def __str__(self) -> str:
        return f'{self.get_kind_display()}: {self.title}'

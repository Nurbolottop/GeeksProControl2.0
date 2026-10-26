from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.clients.models import Client
from apps.common.models import ArchivableModel, TimeStampedModel


class ProjectType(models.Model):
    """Тип проекта. Справочник, пополняется через админку.

    ``is_mobile`` определяет набор этапов разработки: у мобильных проектов
    вместо Frontend идёт «Мобильная разработка».
    """

    name = models.CharField('Название', max_length=100, unique=True)
    is_mobile = models.BooleanField(
        'Мобильная разработка', default=False,
        help_text='У таких проектов этап Frontend заменяется на «Мобильная разработка»',
    )

    class Meta:
        verbose_name = 'Тип проекта'
        verbose_name_plural = 'Типы проектов'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class ProjectStatus(models.TextChoices):
    """Статус проекта — отдельное понятие от этапа (ТЗ §6.3)."""

    ACTIVE = 'active', 'Активный'
    PAUSED = 'paused', 'Приостановлен'
    CANCELLED = 'cancelled', 'Отменён'
    REFUSED = 'refused', 'Клиент отказался'
    COMPLETED = 'completed', 'Завершён'


class ProjectStageKey(models.TextChoices):
    """Этапы жизненного цикла проекта.

    Порядок соответствует реальному процессу GeeksPro: после разработки
    проект выкладывается на тестовый сервер, тестируется и дорабатывается,
    сдаётся заказчику и только потом переносится на боевой сервер.
    """

    NEW = 'new', 'Новый'
    DOCUMENTS = 'documents', 'Документы'
    REQUIREMENTS = 'requirements', 'ТЗ'
    TEAM_FORMING = 'team_forming', 'Формирование команды'
    DESIGN = 'design', 'Дизайн'
    BACKEND = 'backend', 'Backend'
    FRONTEND = 'frontend', 'Frontend'
    MOBILE_DEV = 'mobile_dev', 'Мобильная разработка'
    STAGING = 'staging', 'Тестовый сервер'
    TESTING = 'testing', 'Тестирование'
    REWORK = 'rework', 'Доработка'
    DELIVERY = 'delivery', 'Сдача'
    PRODUCTION = 'production', 'Продакшен'
    COMPLETED = 'completed', 'Завершён'


# Этапы до и после разработки — общие для всех типов проектов
STAGES_BEFORE_DEV = [
    ProjectStageKey.NEW, ProjectStageKey.DOCUMENTS, ProjectStageKey.REQUIREMENTS,
    ProjectStageKey.TEAM_FORMING, ProjectStageKey.DESIGN,
]
STAGES_AFTER_DEV = [
    ProjectStageKey.STAGING, ProjectStageKey.TESTING, ProjectStageKey.REWORK,
    ProjectStageKey.DELIVERY, ProjectStageKey.PRODUCTION,
    ProjectStageKey.COMPLETED,
]


def lifecycle_stages(project_type=None) -> list[str]:
    """Набор этапов под тип проекта.

    Веб-сайт / веб-приложение: … Дизайн → Backend → Frontend → …
    Мобильное приложение:      … Дизайн → Backend → Мобильная разработка → …
    """
    development = [ProjectStageKey.BACKEND]
    if project_type is not None and project_type.is_mobile:
        development.append(ProjectStageKey.MOBILE_DEV)
    else:
        development.append(ProjectStageKey.FRONTEND)
    return [*STAGES_BEFORE_DEV, *development, *STAGES_AFTER_DEV]


class ProjectPriority(models.TextChoices):
    LOW = 'low', 'Низкий'
    MEDIUM = 'medium', 'Средний'
    HIGH = 'high', 'Высокий'
    CRITICAL = 'critical', 'Критический'


class DeadlineStatus(models.TextChoices):
    """Автоматический статус срока (ТЗ §8)."""

    ON_TRACK = 'on_track', 'По графику'
    AT_RISK = 'at_risk', 'Риск задержки'
    BEHIND = 'behind', 'Отставание'
    OVERDUE = 'overdue', 'Просрочен'


class Project(TimeStampedModel, ArchivableModel):
    """Проект — центральная сущность системы (ТЗ §6)."""

    code = models.CharField('Внутренний ID', max_length=20, unique=True, blank=True)
    name = models.CharField('Название', max_length=255)
    client = models.ForeignKey(
        Client, on_delete=models.PROTECT, related_name='projects',
        verbose_name='Заказчик', null=True, blank=True,
    )
    city = models.CharField('Город / филиал', max_length=100, blank=True, db_index=True)
    flow = models.ForeignKey(
        'flows.Flow', on_delete=models.SET_NULL, related_name='projects',
        verbose_name='Поток', null=True, blank=True, db_index=True,
    )
    number_in_flow = models.PositiveSmallIntegerField(
        'Номер в потоке', null=True, blank=True,
        help_text='Например, 1 — тогда ID проекта будет 13.1',
    )
    project_type = models.ForeignKey(
        ProjectType, on_delete=models.PROTECT, related_name='projects',
        verbose_name='Тип проекта', null=True, blank=True,
    )
    description = models.TextField('Описание', blank=True)

    contract_date = models.DateField('Дата подписания договора', null=True, blank=True)
    start_date = models.DateField('Дата начала', null=True, blank=True)
    planned_end_date = models.DateField(
        'Плановая дата завершения', null=True, blank=True, db_index=True,
    )
    actual_end_date = models.DateField(
        'Фактическая дата завершения', null=True, blank=True,
    )

    status = models.CharField(
        'Статус', max_length=20,
        choices=ProjectStatus.choices, default=ProjectStatus.ACTIVE, db_index=True,
    )
    current_stage = models.CharField(
        'Текущий этап', max_length=20,
        choices=ProjectStageKey.choices, default=ProjectStageKey.NEW, db_index=True,
    )
    priority = models.CharField(
        'Приоритет', max_length=20,
        choices=ProjectPriority.choices, default=ProjectPriority.MEDIUM,
    )
    progress = models.PositiveSmallIntegerField('Процент готовности', default=0)

    head_comment = models.TextField('Комментарий руководителя', blank=True)

    # Репозиториев у проекта обычно два — бэкенд и фронтенд (у мобильных —
    # приложение). Хостинг любой: GitLab, GitHub и т.п.
    github_url = models.URLField('Репозиторий бэкенда', blank=True)
    frontend_repo_url = models.URLField('Репозиторий фронтенда', blank=True)
    figma_url = models.URLField('Figma', blank=True)
    staging_url = models.URLField('Staging URL', blank=True)
    production_url = models.URLField('Production URL', blank=True)
    domain = models.CharField('Домен', max_length=255, blank=True)

    is_favorite = models.BooleanField('Избранный', default=False)
    is_problematic = models.BooleanField('Проблемный', default=False)
    last_activity_at = models.DateTimeField('Последняя активность', null=True, blank=True)
    # Когда ПМ последний раз обновил или подтвердил этапы в своём портале.
    # Пока сегодня этого не было, у ПМ горит красное напоминание.
    stages_checked_at = models.DateTimeField(
        'ПМ сверил этапы', null=True, blank=True,
    )
    # Этап проекта (11F: этап один). Его ВРУЧНУЮ выбирает руководитель или ПМ
    # проекта (apps.projects.services.set_primary_stage); при выборе статусы
    # этапов, current_stage и progress выравниваются по нему. Автоматически не
    # меняется. Пусто — этап ещё не выбран. Имя поля — из истории миграций.
    primary_stage = models.ForeignKey(
        'projects.ProjectStage', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Основной этап',
    )
    primary_stage_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Основной этап выбрал',
    )
    primary_stage_set_at = models.DateTimeField('Основной этап выбран', null=True, blank=True)
    # Набор стажёров (2.0). Закрытый набор — проект не показывается в
    # Telegram-боте среди доступных, даже если места есть. Меняет только
    # руководитель (apps.projects.services.set_recruitment). По умолчанию
    # закрыт: открыть набор — явное решение руководителя.
    recruitment_open = models.BooleanField('Набор открыт', default=False, db_index=True)
    recruitment_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Набор изменил',
    )
    recruitment_changed_at = models.DateTimeField('Набор изменён', null=True, blank=True)
    # Готовность к закрытию (UI-03B): три условия фиксирует руководитель —
    # ссылка на production сама по себе не доказывает ни публикацию, ни приёмку.
    dev_completed_at = models.DateTimeField('Разработка завершена', null=True, blank=True)
    dev_completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        verbose_name='Разработку подтвердил',
    )
    production_confirmed_at = models.DateTimeField('Опубликован в production', null=True, blank=True)
    production_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        verbose_name='Публикацию подтвердил',
    )
    client_accepted_at = models.DateTimeField('Принят заказчиком', null=True, blank=True)
    client_accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        verbose_name='Приёмку подтвердил',
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        verbose_name='Закрыл проект',
    )

    class Meta:
        verbose_name = 'Проект'
        verbose_name_plural = 'Проекты'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'current_stage']),
        ]

    def __str__(self) -> str:
        return f'{self.display_code} {self.name}'.strip()

    def clean(self):
        super().clean()
        self._check_primary_stage()

    def _check_primary_stage(self):
        """Основной этап обязан быть этапом этого же проекта."""
        from django.core.exceptions import ValidationError

        if self.primary_stage_id and self.primary_stage.project_id != self.pk:
            raise ValidationError({'primary_stage': 'Этап принадлежит другому проекту.'})

    def save(self, *args, **kwargs):
        if not self.code:
            last_id = Project.objects.aggregate(m=models.Max('id'))['m'] or 0
            self.code = f'GP-{last_id + 1:04d}'
        # Проверяем на любом пути сохранения, а не только в формах
        self._check_primary_stage()
        super().save(*args, **kwargs)

    @property
    def accepts_new_members(self) -> bool:
        """Идёт и набор открыт — новые участники могут вступать."""
        return self.status == ProjectStatus.ACTIVE and self.recruitment_open

    @property
    def display_code(self) -> str:
        """ID проекта в привычном виде: «13.1» (поток.номер)."""
        if self.flow_id and self.number_in_flow:
            return f'{self.flow.number}.{self.number_in_flow}'
        return self.code

    def get_absolute_url(self) -> str:
        return reverse('projects:detail', args=[self.pk])

    # --- Команда: единственный источник правды о ПМ и тимлидах ---
    def _members(self, role: str) -> list:
        return [
            member for member in self.team_members.all()
            if member.role == role and member.status == 'active'
            and member.intern_id
        ]

    @property
    def pm(self):
        """ПМ проекта — участник команды с ролью PM."""
        members = self._members('pm')
        return members[0].intern if members else None

    @property
    def leads(self) -> list:
        """Тимлиды направлений проекта."""
        return [member.intern for member in self._members('team_lead')]

    @property
    def has_pm(self) -> bool:
        return self.pm is not None

    @property
    def duration_days(self):
        """Продолжительность: факт для завершённых, план для остальных."""
        if self.start_date and self.actual_end_date:
            return (self.actual_end_date - self.start_date).days
        if self.start_date and self.planned_end_date:
            return (self.planned_end_date - self.start_date).days
        return None

    @property
    def delay_days(self):
        """Задержка завершённого проекта относительно плана (в днях)."""
        if self.planned_end_date and self.actual_end_date:
            return (self.actual_end_date - self.planned_end_date).days
        return None


class ProjectStage(TimeStampedModel):
    """Этап конкретного проекта (ТЗ §9)."""

    class Status(models.TextChoices):
        NOT_STARTED = 'not_started', 'Не начат'
        IN_PROGRESS = 'in_progress', 'В процессе'
        DONE = 'done', 'Завершён'

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='stages',
        verbose_name='Проект',
    )
    key = models.CharField('Этап', max_length=20, choices=ProjectStageKey.choices)
    order = models.PositiveSmallIntegerField('Порядок', default=0)
    status = models.CharField(
        'Статус', max_length=20,
        choices=Status.choices, default=Status.NOT_STARTED, db_index=True,
    )
    start_date = models.DateField('Дата начала', null=True, blank=True)
    deadline = models.DateField('Deadline', null=True, blank=True, db_index=True)
    end_date = models.DateField('Дата завершения', null=True, blank=True)
    responsible = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='responsible_stages', verbose_name='Ответственный',
        null=True, blank=True,
    )
    progress = models.PositiveSmallIntegerField('Прогресс', default=0)
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        verbose_name = 'Этап проекта'
        verbose_name_plural = 'Этапы проектов'
        ordering = ['order']
        unique_together = [('project', 'key')]

    def __str__(self) -> str:
        return f'{self.project.code}: {self.get_key_display()}'


class ProjectStatusHistory(TimeStampedModel):
    """История изменений проекта (ТЗ §6.4, §27).

    Логирует изменения статуса, этапа, deadline, PM/TL, progress.
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='history',
        verbose_name='Проект',
    )
    field = models.CharField('Поле', max_length=50)
    old_value = models.CharField('Старое значение', max_length=255, blank=True)
    new_value = models.CharField('Новое значение', max_length=255, blank=True)
    reason = models.TextField('Причина', blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Пользователь', null=True, blank=True,
    )

    class Meta:
        verbose_name = 'История проекта'
        verbose_name_plural = 'История проектов'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.project.code}: {self.field}'


class ProjectAccess(TimeStampedModel):
    """Доступы проекта (ТЗ §18: передача доступов).

    Логины/пароли от админки, хостинга, БД и т.п. — загружаются
    на платформу при выходе в прод или сдаче проекта.
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='accesses',
        verbose_name='Проект',
    )
    service = models.CharField(
        'Сервис', max_length=100,
        help_text='Например: Админка сайта, Хостинг, База данных, Домен',
    )
    url = models.CharField('Адрес / URL', max_length=255, blank=True)
    login = models.CharField('Логин', max_length=255, blank=True)
    password = models.CharField('Пароль', max_length=255, blank=True)
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        verbose_name = 'Доступ проекта'
        verbose_name_plural = 'Доступы проектов'
        ordering = ['service']

    def __str__(self) -> str:
        return f'{self.project.code}: {self.service}'


class ProjectReport(TimeStampedModel):
    """Отчёт по проекту: текущее положение, что сделано и что впереди.

    Пишется руками — по итогам недели, собрания или перед сдачей.
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='reports',
        verbose_name='Проект',
    )
    date = models.DateField('Дата отчёта', auto_now_add=True, db_index=True)
    text = models.TextField('Отчёт')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Автор', null=True, blank=True,
    )

    class Meta:
        verbose_name = 'Отчёт по проекту'
        verbose_name_plural = 'Отчёты по проектам'
        ordering = ['-date', '-created_at']

    def __str__(self) -> str:
        return f'{self.project.name} — отчёт от {self.date:%d.%m.%Y}'


class ProjectLink(models.Model):
    """Дополнительная ссылка проекта (ТЗ §6.1)."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='links',
        verbose_name='Проект',
    )
    title = models.CharField('Название', max_length=100)
    url = models.URLField('Ссылка')

    class Meta:
        verbose_name = 'Ссылка проекта'
        verbose_name_plural = 'Ссылки проектов'

    def __str__(self) -> str:
        return self.title

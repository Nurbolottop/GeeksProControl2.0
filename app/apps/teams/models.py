from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.common.models import TimeStampedModel
from apps.projects.models import Project


class TeamRole(models.TextChoices):
    """Структура команды проекта (ТЗ §11)."""

    PROJECT_MANAGER = 'pm', 'Project Manager'
    TEAM_LEAD = 'team_lead', 'Team Lead'
    BACKEND = 'backend', 'Backend'
    FRONTEND = 'frontend', 'Frontend'
    MOBILE = 'mobile', 'Mobile'
    UXUI = 'uxui', 'UX/UI'
    QA = 'qa', 'QA'
    OTHER = 'other', 'Other'


class TeamMember(TimeStampedModel):
    """Участник команды проекта: сотрудник (user) или стажёр (intern)."""

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Активен'
        LEFT = 'left', 'Вышел'

    class LeftReason(models.TextChoices):
        SELF = 'self', 'Сам ушёл'
        UNRESPONSIVE = 'unresponsive', 'Не отвечает'
        NOT_FIT = 'not_fit', 'Не подошёл'
        PROJECT_ENDED = 'project_ended', 'Проект завершён'
        FROZEN = 'frozen', 'Стажировка заморожена'
        EXPELLED = 'expelled', 'Отчислен(а)'
        CANCELLED = 'cancelled', 'Отмена — ушёл(ла) самостоятельно'
        LEFT_PROJECT = 'left_project', 'Вышел(а) из проекта по заявке'
        PROMOTED = 'promoted', 'Повышен(а) до тимлида'
        REPLACED = 'replaced', 'Заменён(а) на проекте'
        DISMISSED = 'dismissed', 'Прекратил(а) работу в GeeksPro'
        EMPLOYED_STUDIO = 'studio', 'Трудоустроен(а) в Geeks Studio'
        OTHER = 'other', 'Другое'

    group = models.ForeignKey(
        'flows.Group', on_delete=models.CASCADE, related_name='members',
        verbose_name='Группа', null=True, blank=True,
    )
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='team_members',
        verbose_name='Проект', null=True, blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='team_memberships', verbose_name='Сотрудник',
        null=True, blank=True,
    )
    intern = models.ForeignKey(
        'interns.Intern', on_delete=models.CASCADE,
        related_name='team_memberships', verbose_name='Стажёр',
        null=True, blank=True,
    )
    role = models.CharField('Роль', max_length=20, choices=TeamRole.choices)
    joined_at = models.DateField('Дата подключения', null=True, blank=True)
    left_at = models.DateField('Дата выхода', null=True, blank=True)
    workload = models.PositiveSmallIntegerField('Загрузка, %', default=50)
    status = models.CharField(
        'Статус', max_length=10,
        choices=Status.choices, default=Status.ACTIVE, db_index=True,
    )
    left_reason = models.CharField(
        'Причина выхода', max_length=20, choices=LeftReason.choices, blank=True,
    )
    comment = models.TextField('Комментарий', blank=True)
    # Тимлид (этап 8B): какое направление проекта ведёт это назначение.
    # Пусто — направление по специализации тимлида (как было до 8B).
    # Тимлид двух направлений в одном проекте — два назначения.
    lead_direction = models.CharField('Направление тимлида', max_length=20, blank=True)

    class Meta:
        verbose_name = 'Участник команды'
        verbose_name_plural = 'Участники команд'
        ordering = ['role']

    def __str__(self) -> str:
        return f'{self.person_name} — {self.get_role_display()} ({self.project.code})'

    def save(self, *args, **kwargs):
        # Проект берётся из группы: одна группа ведёт один проект
        if self.group_id and self.group.project_id:
            self.project_id = self.group.project_id
        super().save(*args, **kwargs)

    def clean(self):
        if not self.user and not self.intern:
            raise ValidationError('Укажите участника команды.')
        if self.user and self.intern:
            raise ValidationError('Участник должен быть указан только один раз.')

    @property
    def person_name(self) -> str:
        if self.user:
            return self.user.display_name
        if self.intern:
            return self.intern.full_name
        return '—'

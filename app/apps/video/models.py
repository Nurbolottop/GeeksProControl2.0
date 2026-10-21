"""Видеособрания (этап 9): встреча, приглашённые, события видеопровайдера,
ручные исправления посещаемости.

Личность участника подтверждается только тем, что провайдер вернул в событии
наш собственный userId (provider_user_id) — его знает только персональная
ссылка этого человека (JWT подписывает Control). Отображаемое имя участника
в видеосервисе ничего не подтверждает. События провайдера хранятся как
есть и не изменяются; исправления — отдельными записями.
"""
import secrets

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


def new_room() -> str:
    return f'gp2-{secrets.token_hex(10)}'


def new_user_id() -> str:
    return f'gp2u-{secrets.token_hex(12)}'


def new_join_token() -> str:
    return secrets.token_urlsafe(24)


class VideoMeeting(TimeStampedModel):
    class Kind(models.TextChoices):
        REQUIRED = 'required', 'Обязательное собрание направления'
        EXTRA = 'extra', 'Дополнительное собрание направления'
        PM_LEADS = 'pm_leads', 'Встреча ПМ с тимлидами'

    class Status(models.TextChoices):
        PLANNED = 'planned', 'Запланировано'
        HELD = 'held', 'Проведено'
        CANCELLED = 'cancelled', 'Отменено'

    class HeldSource(models.TextChoices):
        PROVIDER = 'provider', 'По событиям видеосервиса'
        MANUAL = 'manual', 'Отмечено вручную'

    project = models.ForeignKey('projects.Project', on_delete=models.PROTECT, related_name='video_meetings',
                                verbose_name='Проект')
    direction = models.CharField('Направление', max_length=20, blank=True, db_index=True)
    kind = models.CharField('Вид', max_length=10, choices=Kind.choices, default=Kind.REQUIRED, db_index=True)
    topic = models.CharField('Тема', max_length=255)
    starts_at = models.DateTimeField('Начало', db_index=True)
    duration_minutes = models.PositiveSmallIntegerField('Длительность (план), мин', default=60)
    week_start = models.DateField('Неделя (понедельник)', db_index=True)
    host = models.ForeignKey('interns.Intern', on_delete=models.PROTECT, related_name='video_meetings_hosted',
                             verbose_name='Ведущий (тимлид / ПМ)')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='Создал')
    status = models.CharField('Статус', max_length=10, choices=Status.choices, default=Status.PLANNED, db_index=True)
    held_source = models.CharField('Чем подтверждено проведение', max_length=10, choices=HeldSource.choices,
                                   blank=True)
    held_at = models.DateTimeField('Проведено', null=True, blank=True)
    held_note = models.TextField('Причина ручной отметки', blank=True)
    held_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+', verbose_name='Отметил проведённым')
    cancelled_reason = models.TextField('Причина отмены', blank=True)
    provider = models.CharField('Видеосервис', max_length=20)
    room = models.CharField('Комната', max_length=64, unique=True, default=new_room)
    room_started_at = models.DateTimeField('Комната открыта (провайдер)', null=True, blank=True)
    room_ended_at = models.DateTimeField('Комната закрыта (провайдер)', null=True, blank=True)
    host_user_id = models.CharField('userId ведущего в видеосервисе', max_length=40, unique=True,
                                    default=new_user_id)

    class Meta:
        verbose_name = 'Видеособрание'
        verbose_name_plural = 'Видеособрания'
        ordering = ['-starts_at']

    def __str__(self) -> str:
        return f'{self.topic} ({self.starts_at:%d.%m.%Y %H:%M})'

    @property
    def is_direction_meeting(self) -> bool:
        return self.kind in (self.Kind.REQUIRED, self.Kind.EXTRA)


class MeetingParticipant(TimeStampedModel):
    """Приглашённый. join_token — секрет персональной ссылки; provider_user_id —
    наш userId в JWT: только по нему событие провайдера подтверждает личность."""

    meeting = models.ForeignKey(VideoMeeting, on_delete=models.CASCADE, related_name='participants',
                                verbose_name='Собрание')
    intern = models.ForeignKey('interns.Intern', on_delete=models.PROTECT, related_name='video_invitations',
                               verbose_name='Участник')
    person_name = models.CharField('ФИО', max_length=255)
    role = models.CharField('Роль в проекте', max_length=20, blank=True)
    provider_user_id = models.CharField('userId в видеосервисе', max_length=40, unique=True, default=new_user_id)
    join_token = models.CharField('Токен ссылки', max_length=64, unique=True, default=new_join_token)
    invited_at = models.DateTimeField('Приглашение поставлено', null=True, blank=True)
    link_opened_at = models.DateTimeField('Ссылка открыта (не посещение)', null=True, blank=True)
    removed_at = models.DateTimeField('Исключён из встречи', null=True, blank=True)

    class Meta:
        verbose_name = 'Участник видеособрания'
        verbose_name_plural = 'Участники видеособраний'
        ordering = ['person_name']
        constraints = [models.UniqueConstraint(fields=['meeting', 'intern'], name='video_participant_once')]

    def __str__(self) -> str:
        return f'{self.person_name} — {self.meeting}'


class ProviderEvent(models.Model):
    """Событие видеопровайдера — как пришло (без email), только добавляется."""

    class Verification(models.TextChoices):
        VERIFIED = 'verified', 'Личность подтверждена (наш userId)'
        HOST = 'host', 'Ведущий (наш userId)'
        UNVERIFIED = 'unverified', 'Личность не подтверждена'
        ROOM = 'room', 'Событие комнаты'
        UNKNOWN_ROOM = 'unknown_room', 'Комната не из Control'

    provider = models.CharField('Видеосервис', max_length=20)
    idempotency_key = models.CharField('Ключ события', max_length=128, unique=True)
    event_type = models.CharField('Событие', max_length=40, db_index=True)
    meeting = models.ForeignKey(VideoMeeting, on_delete=models.PROTECT, null=True, blank=True,
                                related_name='events', verbose_name='Собрание')
    participant = models.ForeignKey(MeetingParticipant, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='events', verbose_name='Участник GeeksPro')
    provider_user_id = models.CharField('userId (из JWT)', max_length=64, blank=True)
    provider_participant_id = models.CharField('participantId провайдера', max_length=128, blank=True)
    display_name = models.CharField('Имя в видеосервисе (не доказательство)', max_length=255, blank=True)
    session_id = models.CharField('Сессия провайдера', max_length=128, blank=True)
    disconnect_reason = models.CharField('Причина отключения', max_length=40, blank=True)
    verification = models.CharField('Достоверность', max_length=15, choices=Verification.choices)
    occurred_at = models.DateTimeField('Время события (провайдер)')
    received_at = models.DateTimeField('Получено', auto_now_add=True)

    class Meta:
        verbose_name = 'Событие видеосервиса'
        verbose_name_plural = 'События видеосервиса'
        ordering = ['occurred_at', 'pk']

    def __str__(self) -> str:
        return f'{self.event_type} {self.occurred_at:%d.%m %H:%M}'


class AttendanceCorrection(models.Model):
    """Ручное исправление посещаемости: кто, кого, было → стало, причина, когда.
    События провайдера не меняются; действует последнее исправление."""

    class Value(models.TextChoices):
        PRESENT = 'present', 'Присутствовал(а)'
        ABSENT = 'absent', 'Отсутствовал(а)'
        EXCUSED = 'excused', 'Уважительная причина'

    participant = models.ForeignKey(MeetingParticipant, on_delete=models.PROTECT, related_name='corrections',
                                    verbose_name='Участник')
    old_value = models.CharField('Было', max_length=20)
    new_value = models.CharField('Стало', max_length=10, choices=Value.choices)
    reason = models.TextField('Причина')
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+',
                                   verbose_name='Изменил')
    changed_at = models.DateTimeField('Когда', auto_now_add=True)

    class Meta:
        verbose_name = 'Исправление посещаемости'
        verbose_name_plural = 'Исправления посещаемости'
        ordering = ['changed_at', 'pk']

    def __str__(self) -> str:
        return f'{self.participant}: {self.old_value} → {self.new_value}'


class EntryGrant(models.Model):
    """Одноразовый вход на собрание (этап 10A): выдаётся только после нажатия
    кнопки в боте из подтверждённого Telegram участника; живёт 5 минут и
    обменивается на JWT один раз. Хранится хеш кода, не сам код."""

    participant = models.ForeignKey(MeetingParticipant, on_delete=models.CASCADE, related_name='entry_grants',
                                    verbose_name='Участник')
    code_hash = models.CharField('Хеш кода', max_length=64, unique=True)
    telegram_chat_id = models.BigIntegerField('Подтверждено из Telegram')
    created_at = models.DateTimeField('Выдан', auto_now_add=True)
    expires_at = models.DateTimeField('Действует до')
    used_at = models.DateTimeField('Использован', null=True, blank=True)

    class Meta:
        verbose_name = 'Вход на собрание (одноразовый)'
        verbose_name_plural = 'Входы на собрания'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.participant} · {self.created_at:%d.%m %H:%M}'

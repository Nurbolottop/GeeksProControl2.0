"""Telegram-бот 2.0 (этап 8): журнал входящих обновлений, состояние диалога,
очередь и журнал исходящих сообщений, ручная сверка привязки профиля.

Состояние диалога хранится в БД (DialogState), а не в памяти процесса:
перезапуск бота не теряет шаг регистрации или сдачи работы.
"""
from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class TelegramUpdate(models.Model):
    """Каждый update_id обрабатывается один раз (повторы Telegram — игнор)."""

    update_id = models.BigIntegerField('update_id', unique=True)
    kind = models.CharField('Тип', max_length=30, blank=True)
    chat_id = models.BigIntegerField('Чат', null=True, blank=True, db_index=True)
    user_id = models.BigIntegerField('Отправитель', null=True, blank=True)
    outcome = models.CharField('Результат', max_length=40, blank=True)
    received_at = models.DateTimeField('Получено', auto_now_add=True)

    class Meta:
        verbose_name = 'Входящее обновление Telegram'
        verbose_name_plural = 'Входящие обновления Telegram'
        ordering = ['-received_at']

    def __str__(self) -> str:
        return f'update {self.update_id}: {self.outcome}'


class DialogState(models.Model):
    """Текущий шаг диалога в личном чате (переживает перезапуск бота)."""

    chat_id = models.BigIntegerField('Чат', unique=True)
    telegram_user_id = models.BigIntegerField('Пользователь Telegram', null=True, blank=True)
    state = models.CharField('Шаг', max_length=40, blank=True)
    data = models.JSONField('Данные шага', default=dict, blank=True)
    updated_at = models.DateTimeField('Обновлено', auto_now=True)

    class Meta:
        verbose_name = 'Состояние диалога'
        verbose_name_plural = 'Состояния диалогов'

    def __str__(self) -> str:
        return f'{self.chat_id}: {self.state or "меню"}'


class OutboundMessage(TimeStampedModel):
    """Очередь и журнал исходящих: каждое сообщение бота — запись здесь.

    Отправка — только через GuardedTeleBot (apps.common.outbound): вне
    разрешённого режима сообщение остаётся «Задержано защитой» и никуда
    не уходит. dedup_key не даёт отправить одно уведомление дважды."""

    class Status(models.TextChoices):
        QUEUED = 'queued', 'В очереди'
        SENT = 'sent', 'Отправлено'
        HELD = 'held', 'Задержано защитой (stage)'
        NO_CHAT = 'no_chat', 'Нет Telegram — человек не начинал диалог'
        BOT_BLOCKED = 'blocked', 'Бот заблокирован получателем'
        RETRY = 'retry', 'Временная ошибка — повтор'
        FAILED = 'failed', 'Не доставлено'

    kind = models.CharField('Вид', max_length=40, db_index=True)
    chat_id = models.BigIntegerField('Чат', null=True, blank=True, db_index=True)
    message_thread_id = models.BigIntegerField('Тема', null=True, blank=True)
    intern = models.ForeignKey('interns.Intern', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='telegram_messages', verbose_name='Получатель')
    text = models.TextField('Текст')
    reply_markup = models.JSONField('Клавиатура', null=True, blank=True)
    dedup_key = models.CharField('Ключ от дублей', max_length=200, null=True, blank=True, unique=True)
    status = models.CharField('Статус', max_length=10, choices=Status.choices, default=Status.QUEUED,
                              db_index=True)
    attempts = models.PositiveSmallIntegerField('Попыток', default=0)
    next_attempt_at = models.DateTimeField('Следующая попытка', null=True, blank=True)
    last_error = models.CharField('Последняя ошибка', max_length=255, blank=True)
    sent_at = models.DateTimeField('Отправлено', null=True, blank=True)
    telegram_message_id = models.BigIntegerField('message_id', null=True, blank=True)

    class Meta:
        verbose_name = 'Исходящее сообщение Telegram'
        verbose_name_plural = 'Исходящие сообщения Telegram'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.kind} → {self.chat_id}: {self.get_status_display()}'


class LinkRequest(TimeStampedModel):
    """«Я уже стажёр GeeksPro» без однозначного совпадения — ручная сверка
    руководителем. Новый профиль не создаётся, статус стажёра не меняется."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Ждёт сверки'
        LINKED = 'linked', 'Привязан к профилю'
        REJECTED = 'rejected', 'Отклонён'

    chat_id = models.BigIntegerField('Чат')
    telegram_user_id = models.BigIntegerField('Пользователь Telegram')
    telegram_username = models.CharField('Username', max_length=100, blank=True)
    full_name = models.CharField('ФИО (назвал)', max_length=255)
    phone = models.CharField('Телефон (контакт Telegram)', max_length=32)
    reason = models.CharField('Почему сверка', max_length=255)
    candidates = models.ManyToManyField('interns.Intern', blank=True, related_name='+',
                                        verbose_name='Возможные профили')
    status = models.CharField('Статус', max_length=10, choices=Status.choices, default=Status.PENDING,
                              db_index=True)
    intern = models.ForeignKey('interns.Intern', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='+', verbose_name='Привязан к')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='Решил')
    decided_at = models.DateTimeField('Решение', null=True, blank=True)

    class Meta:
        verbose_name = 'Сверка Telegram-профиля'
        verbose_name_plural = 'Сверки Telegram-профилей'
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['telegram_user_id'], condition=models.Q(status='pending'),
                                               name='one_pending_link_request')]

    def __str__(self) -> str:
        return f'{self.full_name} ({self.get_status_display()})'


# ============================================================ этап 8B: группы

class TelegramGroup(TimeStampedModel):
    """Супергруппа Telegram, привязанная в Control: проектная (с темами по
    направлениям) или служебная (все тимлиды / все ПМ). Группу создаёт
    руководитель вручную; бот только проверяет права и создаёт темы.
    Группа и темы — не граница доступа к данным: доступ решает Control."""

    class Kind(models.TextChoices):
        PROJECT = 'project', 'Проект'
        LEADS = 'leads', 'Все тимлиды GeeksPro'
        PMS = 'pms', 'Все ПМ GeeksPro'
        SALES = 'sales', 'Отдел продаж'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Ждёт подтверждения руководителя'
        ACTIVE = 'active', 'Привязана'
        UNBOUND = 'unbound', 'Отвязана'

    kind = models.CharField('Вид', max_length=10, choices=Kind.choices, default=Kind.PROJECT)
    project = models.ForeignKey('projects.Project', on_delete=models.PROTECT, null=True, blank=True,
                                related_name='telegram_groups', verbose_name='Проект')
    chat_id = models.BigIntegerField('chat_id')
    title = models.CharField('Название в Telegram', max_length=255, blank=True)
    is_forum = models.BooleanField('Темы включены', default=False)
    can_manage_topics = models.BooleanField('Бот может управлять темами', default=False)
    status = models.CharField('Статус', max_length=10, choices=Status.choices, default=Status.PENDING,
                              db_index=True)
    requested_by_telegram_id = models.BigIntegerField('Кто отправил код (Telegram id)', null=True, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', verbose_name='Подтвердил')
    confirmed_at = models.DateTimeField('Подтверждено', null=True, blank=True)
    unbound_at = models.DateTimeField('Отвязана', null=True, blank=True)
    last_error = models.CharField('Последняя ошибка', max_length=255, blank=True)

    class Meta:
        verbose_name = 'Telegram-группа'
        verbose_name_plural = 'Telegram-группы'
        ordering = ['kind', 'project__name']
        constraints = [
            # одна группа — один проект; повторная привязка — только после отвязки
            models.UniqueConstraint(fields=['chat_id'], condition=~models.Q(status='unbound'),
                                    name='tg_group_chat_once'),
            models.UniqueConstraint(fields=['project'], condition=models.Q(kind='project') & ~models.Q(status='unbound'),
                                    name='tg_group_one_per_project'),
            models.UniqueConstraint(fields=['kind'], condition=~models.Q(kind='project') & ~models.Q(status='unbound'),
                                    name='tg_group_one_service_each'),
        ]

    def __str__(self) -> str:
        return self.title or str(self.chat_id)


class BindCode(TimeStampedModel):
    """Одноразовый код привязки группы: выпускает руководитель в Control,
    отправляет в группе командой /bind КОД. Живёт 30 минут."""

    code = models.CharField('Код', max_length=20, unique=True)
    kind = models.CharField('Вид группы', max_length=10, choices=TelegramGroup.Kind.choices)
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, null=True, blank=True,
                                related_name='+', verbose_name='Проект')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   related_name='+', verbose_name='Выпустил')
    expires_at = models.DateTimeField('Действует до')
    used_at = models.DateTimeField('Использован', null=True, blank=True)
    group = models.ForeignKey(TelegramGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                              verbose_name='Группа')

    class Meta:
        verbose_name = 'Код привязки группы'
        verbose_name_plural = 'Коды привязки групп'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return self.code


class GroupTopic(TimeStampedModel):
    """Тема (forum topic) проектной группы — по направлению или объявления."""

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Есть'
        MISSING = 'missing', 'Удалена или недоступна'
        ERROR = 'error', 'Не создана (ошибка)'

    group = models.ForeignKey(TelegramGroup, on_delete=models.CASCADE, related_name='topics', verbose_name='Группа')
    key = models.CharField('Направление', max_length=20)
    title = models.CharField('Название', max_length=128)
    message_thread_id = models.BigIntegerField('message_thread_id', null=True, blank=True)
    status = models.CharField('Статус', max_length=10, choices=Status.choices, default=Status.ACTIVE)
    last_error = models.CharField('Последняя ошибка', max_length=255, blank=True)

    class Meta:
        verbose_name = 'Тема группы'
        verbose_name_plural = 'Темы групп'
        ordering = ['group', 'key']
        constraints = [models.UniqueConstraint(fields=['group', 'key'], name='tg_topic_once')]

    def __str__(self) -> str:
        return f'{self.group}: {self.title}'


class GroupMembership(TimeStampedModel):
    """Подключение участника проекта к группе проекта — по фактам: тимлид
    отмечает приглашение, вступление подтверждает Telegram (chat_member) или
    тимлид. Ссылка не считается вступлением. Выход из проекта — «доступ
    прекращён» (тимлид удаляет из группы); история не удаляется."""

    class Status(models.TextChoices):
        TO_INVITE = 'to_invite', 'Нужно пригласить'
        INVITED = 'invited', 'Приглашение отправлено'
        JOINED = 'joined', 'В группе'
        ACCESS_ENDED = 'ended', 'Участие закончено — удалить из группы'
        REMOVED = 'removed', 'Удалён(а) из группы'

    member = models.OneToOneField('teams.TeamMember', on_delete=models.CASCADE, related_name='telegram_access',
                                  verbose_name='Участие')
    group = models.ForeignKey(TelegramGroup, on_delete=models.PROTECT, related_name='memberships',
                              verbose_name='Группа')
    status = models.CharField('Статус', max_length=10, choices=Status.choices, default=Status.TO_INVITE,
                              db_index=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='Пригласил')
    invited_at = models.DateTimeField('Приглашён', null=True, blank=True)
    joined_at = models.DateTimeField('Вступил', null=True, blank=True)
    join_source = models.CharField('Чем подтверждено', max_length=20, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', verbose_name='Подтвердил')
    ended_at = models.DateTimeField('Участие закончено', null=True, blank=True)
    removed_at = models.DateTimeField('Удалён из группы', null=True, blank=True)

    class Meta:
        verbose_name = 'Участник в Telegram-группе'
        verbose_name_plural = 'Участники в Telegram-группах'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.member} — {self.get_status_display()}'

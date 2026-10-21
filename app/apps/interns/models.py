import secrets

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse

from apps.common.models import ArchivableModel, TimeStampedModel
from apps.training.models import Specialization, TrainingGroup


class InternStatus(models.TextChoices):
    """Статусы стажёра (ТЗ §12)."""

    WAITING = 'waiting', 'Ожидает стажировки'
    READY = 'ready', 'Готов к распределению'
    ACTIVE = 'active', 'Активный'
    PAUSED = 'paused', 'Приостановлен'
    EMPLOYABLE = 'employable', 'Готов к трудоустройству'
    EMPLOYED = 'employed', 'Трудоустроен'
    DROPPED = 'dropped', 'Выбыл'
    EXPELLED = 'expelled', 'Отчислен'
    CANCELLED = 'cancelled', 'Отмена — ушёл самостоятельно'


# Статусы, при которых стажёр считается работающим в GeeksPro
WORKING_STATUSES = (InternStatus.ACTIVE,)

# Кого можно поставить на проект прямо сейчас. Замороженных, не начавших
# стажировку и выпускников (их ещё не разобрали) свободными не считаем.
AVAILABLE_STATUSES = (
    InternStatus.ACTIVE, InternStatus.READY, InternStatus.EMPLOYABLE,
)


class Branch(models.TextChoices):
    """Филиал — сейчас у студии их два."""

    BISHKEK = 'Бишкек', 'Бишкек'
    OSH = 'Ош', 'Ош'


class Position(models.TextChoices):
    """Должность человека у нас — свойство человека, а не проекта.

    Повысили до тимлида — он тимлид везде: на всех своих проектах и на
    всех будущих, пока его не снимут обратно или он не уйдёт. Раньше роль
    жила только внутри команды, и на соседнем проекте тот же человек
    снова оказывался обычным стажёром.
    """

    INTERN = 'intern', 'Стажёр'
    TEAM_LEAD = 'lead', 'Тимлид'
    PM = 'pm', 'Проект-менеджер'


class StaffStatus(models.TextChoices):
    """Кадровый статус сотрудника GeeksPro (этап 6) — отдельно от статуса
    стажировки, направления и проектных ролей."""

    ACTIVE = 'active', 'Работает в GeeksPro'
    DISMISSED = 'dismissed', 'Прекратил(а) работу в GeeksPro'
    STUDIO = 'studio', 'Трудоустроен(а) в Geeks Studio'


class StaffRole(models.TextChoices):
    LEAD = 'lead', 'Тимлид'
    PM = 'pm', 'ПМ (сотрудник)'


class GraduateStatus(models.TextChoices):
    """Куда делся стажёр после завершения проекта (ТЗ: разбор «Выпускников»).

    Пусто — не выпускник прямо сейчас (либо ещё не выпускался, либо уже
    назначен на новый проект — тогда из «Выпускников» пропадает совсем).
    """

    PENDING = 'pending', 'На проверке'
    DECLINED = 'declined', 'Не хочет продолжать'
    # Хочет продолжить, но свободных проектов сейчас нет — ждёт, пока
    # появится место (отмечает себя сам через бота-выпускника).
    WAITING = 'waiting', 'Ждёт проект'


class ResumeBankStatus(models.TextChoices):
    """Статус проверки заявки в банк резюме — независимо от того, как
    человек попал в банк (бот-выпускник или старая публичная форма)."""

    PENDING = 'pending', 'Ожидает проверки'
    APPROVED = 'approved', 'Принят'
    REVISION = 'revision', 'На доработке'


class Intern(TimeStampedModel, ArchivableModel):
    """Карточка стажёра (ТЗ §12)."""

    full_name = models.CharField('ФИО', max_length=255)
    phone = models.CharField('Телефон', max_length=32, blank=True)
    email = models.EmailField('Email', blank=True)
    telegram = models.CharField('Telegram', max_length=100, blank=True)
    city = models.CharField('Город', max_length=100, blank=True)
    branch = models.CharField(
        'Филиал', max_length=20, choices=Branch.choices, blank=True,
    )
    specialization = models.ForeignKey(
        Specialization, on_delete=models.PROTECT, related_name='interns',
        verbose_name='Направление', null=True, blank=True, db_index=True,
    )
    training_group = models.ForeignKey(
        TrainingGroup, on_delete=models.SET_NULL, related_name='interns',
        verbose_name='Учебная группа', null=True, blank=True,
    )
    flow = models.ForeignKey(
        'flows.Flow', on_delete=models.SET_NULL, related_name='interns',
        verbose_name='Поток', null=True, blank=True, db_index=True,
    )
    education_end_date = models.DateField(
        'Дата окончания обучения', null=True, blank=True,
    )
    internship_start_date = models.DateField(
        'Дата начала стажировки', null=True, blank=True,
    )
    internship_attempt = models.PositiveSmallIntegerField(
        'Какая по счёту стажировка', default=1,
        validators=[MinValueValidator(1)],
    )
    status = models.CharField(
        'Статус', max_length=20,
        choices=InternStatus.choices, default=InternStatus.WAITING, db_index=True,
    )
    team_lead = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='mentored_interns', verbose_name='Текущий Team Lead',
        null=True, blank=True,
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='intern_profile', verbose_name='Учётная запись',
        null=True, blank=True,
    )
    in_resume_bank = models.BooleanField('В банке резюме', default=False)
    resume_bank_status = models.CharField(
        'Статус банка резюме', max_length=10,
        choices=ResumeBankStatus.choices, blank=True,
    )
    resume_bank_comment = models.TextField(
        'Комментарий по доработке (банк резюме)', blank=True,
    )
    graduate_status = models.CharField(
        'Статус выпускника', max_length=10,
        choices=GraduateStatus.choices, blank=True,
    )
    telegram_chat_id = models.BigIntegerField(
        'Telegram chat id', null=True, blank=True, db_index=True,
    )
    phone_lock_until = models.DateTimeField(
        'Бот-выпускник: блокировка проверки телефона до', null=True, blank=True,
    )
    position = models.CharField(
        'Должность', max_length=10, choices=Position.choices,
        default=Position.INTERN, db_index=True,
        help_text='Тимлид остаётся тимлидом на всех проектах, пока его не снимут.',
    )
    # Когда бот последний раз напоминал о себе — чтобы не писать каждый день.
    bot_reminded_at = models.DateTimeField(
        'Бот-выпускник: последнее напоминание', null=True, blank=True,
    )
    # Правила бота человек читает один раз: дальше сразу вход в аккаунт.
    rules_accepted_at = models.DateTimeField(
        'Бот-выпускник: ознакомлен с правилами', null=True, blank=True,
    )
    rating = models.DecimalField(
        'Рейтинг', max_digits=3, decimal_places=2, null=True, blank=True,
    )
    comment = models.TextField('Комментарий', blank=True)
    # Кадровый статус сотрудника (этап 6). Пусто — не сотрудник. Меняется
    # только сервисами apps.teams.staff и apps.reserve.employment.
    staff_status = models.CharField('Кадровый статус', max_length=10, choices=StaffStatus.choices, blank=True,
                                    db_index=True)
    staff_role = models.CharField('Должность сотрудника', max_length=10, choices=StaffRole.choices, blank=True)
    staff_since = models.DateField('Сотрудник с', null=True, blank=True)
    staff_until = models.DateField('Сотрудник по', null=True, blank=True)

    class Meta:
        verbose_name = 'Стажёр'
        verbose_name_plural = 'Стажёры'
        ordering = ['full_name']

    def __str__(self) -> str:
        return self.full_name

    def get_absolute_url(self) -> str:
        return reverse('interns:detail', args=[self.pk])

    @property
    def active_memberships(self):
        return [
            m for m in self.team_memberships.all() if m.status == 'active'
        ]


class TalentReserveCandidate(TimeStampedModel, ArchivableModel):
    """Резерв кадров — отдельный пул людей, не привязан к карточке стажёра.

    По факту сюда обычно попадают наши же бывшие стажёры/тимлиды, но
    запись самостоятельная: нет связи с Intern, свой набор полей.
    """

    full_name = models.CharField('ФИО', max_length=255)
    phone = models.CharField('Телефон', max_length=32, blank=True)
    email = models.EmailField('Email', blank=True)
    telegram = models.CharField('Telegram', max_length=100, blank=True)
    city = models.CharField('Город', max_length=100, blank=True)
    specialization = models.ForeignKey(
        Specialization, on_delete=models.PROTECT, related_name='reserve_candidates',
        verbose_name='Направление', null=True, blank=True,
    )
    desired_role = models.CharField('Желаемая роль/позиция', max_length=255, blank=True)
    experience = models.TextField('О себе / опыт', blank=True)
    portfolio_link = models.URLField('Портфолио / резюме — ссылка', blank=True)
    priority = models.PositiveIntegerField(
        'Приоритет', default=0,
        help_text='Чем больше число, тем выше в списке.',
    )
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        verbose_name = 'Кандидат в резерве'
        verbose_name_plural = 'Резерв кадров'
        ordering = ['-priority', '-created_at']

    def __str__(self) -> str:
        return self.full_name


class InternEvaluation(TimeStampedModel):
    """Оценка стажёра по 7 критериям, шкала 1–5 (ТЗ §12.1)."""

    CRITERIA = [
        ('hard_skills', 'Hard skills'),
        ('quality', 'Качество работы'),
        ('speed', 'Скорость'),
        ('responsibility', 'Ответственность'),
        ('communication', 'Коммуникация'),
        ('teamwork', 'Teamwork'),
        ('independence', 'Самостоятельность'),
    ]
    SCORE_CHOICES = [(i, str(i)) for i in range(1, 6)]

    intern = models.ForeignKey(
        Intern, on_delete=models.CASCADE, related_name='evaluations',
        verbose_name='Стажёр',
    )
    project = models.ForeignKey(
        'projects.Project', on_delete=models.SET_NULL, related_name='+',
        verbose_name='Проект', null=True, blank=True,
    )
    evaluator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Оценил', null=True,
    )
    hard_skills = models.PositiveSmallIntegerField('Hard skills', choices=SCORE_CHOICES)
    quality = models.PositiveSmallIntegerField('Качество работы', choices=SCORE_CHOICES)
    speed = models.PositiveSmallIntegerField('Скорость', choices=SCORE_CHOICES)
    responsibility = models.PositiveSmallIntegerField('Ответственность', choices=SCORE_CHOICES)
    communication = models.PositiveSmallIntegerField('Коммуникация', choices=SCORE_CHOICES)
    teamwork = models.PositiveSmallIntegerField('Teamwork', choices=SCORE_CHOICES)
    independence = models.PositiveSmallIntegerField('Самостоятельность', choices=SCORE_CHOICES)
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        verbose_name = 'Оценка стажёра'
        verbose_name_plural = 'Оценки стажёров'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'Оценка {self.intern} от {self.created_at:%d.%m.%Y}'

    @property
    def average(self) -> float:
        scores = [getattr(self, key) for key, _ in self.CRITERIA]
        return round(sum(scores) / len(scores), 2)


def generate_form_token() -> str:
    """Случайный токен для ссылки на анкету."""
    return secrets.token_urlsafe(16)


class ProfileFormLink(TimeStampedModel):
    """Сменяемая ссылка на публичную анкету стажёра.

    Постоянного адреса у анкеты нет: новая ссылка гасит прежние — чтобы
    форма не гуляла по чатам вечно и её не заполняли посторонние.

    Ссылка проекта (``project``) — её выпускает тимлид для новых
    стажёров своей команды: кто заполнил анкету, сразу попадает в
    команду этого проекта. У каждого проекта своя действующая ссылка.
    Ссылка без проекта — общая, из списка «Все стажёры».
    """

    token = models.CharField(
        'Токен', max_length=64, unique=True, default=generate_form_token,
    )
    is_active = models.BooleanField('Активна', default=True)
    expires_at = models.DateTimeField(
        'Действует до', null=True, blank=True,
        help_text='Пусто — ссылка живёт, пока её не заменят или не отключат.',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Создал', null=True, blank=True,
    )
    submissions = models.PositiveIntegerField('Заполнений', default=0)
    deactivated_at = models.DateTimeField(
        'Отключена', null=True, blank=True,
    )
    project = models.ForeignKey(
        'projects.Project', on_delete=models.CASCADE, related_name='profile_links',
        verbose_name='Проект', null=True, blank=True,
        help_text='Кто заполнит анкету по этой ссылке — сразу в команду проекта.',
    )

    class Meta:
        verbose_name = 'Ссылка на анкету стажёра'
        verbose_name_plural = 'Ссылки на анкету стажёра'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'Анкета стажёра /{self.token}/'

    def get_absolute_url(self) -> str:
        return reverse('intern_profile_apply', args=[self.token])

    def deactivate(self) -> None:
        from django.utils import timezone

        self.is_active = False
        self.deactivated_at = timezone.now()
        self.save(update_fields=['is_active', 'deactivated_at', 'updated_at'])

    @property
    def is_expired(self) -> bool:
        from django.utils import timezone

        return bool(self.expires_at and self.expires_at <= timezone.now())

    @property
    def is_open(self) -> bool:
        """Анкета по ссылке открывается только пока она активна и не истекла."""
        return self.is_active and not self.is_expired


class ProfileFormSubmission(TimeStampedModel):
    """Журнал заполнений анкеты — «ответы» по каждой ссылке.

    Сами данные уходят в карточку стажёра, здесь остаётся след: кто,
    когда и по какой ссылке прошёл анкету, завели новую карточку или
    обновили существующую.
    """

    link = models.ForeignKey(
        ProfileFormLink, on_delete=models.CASCADE, related_name='submissions_log',
        verbose_name='Ссылка',
    )
    intern = models.ForeignKey(
        Intern, on_delete=models.SET_NULL, related_name='form_submissions',
        verbose_name='Стажёр', null=True, blank=True,
    )
    full_name = models.CharField('ФИО', max_length=255)
    phone = models.CharField('Телефон', max_length=32, blank=True)
    is_new = models.BooleanField('Новая карточка', default=False)

    class Meta:
        verbose_name = 'Заполнение анкеты'
        verbose_name_plural = 'Заполнения анкеты'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.full_name} — {self.created_at:%d.%m.%Y %H:%M}'


class InternshipRequest(TimeStampedModel):
    """Заявка стажёра на заморозку или возвращение (UI-03C).

    Подаёт стажёр (через Telegram-бота) или руководитель со слов стажёра;
    решает только руководитель (apps.interns.freeze). Пока заявка не
    одобрена, участие в проектах не меняется.
    """

    class Kind(models.TextChoices):
        FREEZE = 'freeze', 'Заморозка'
        RETURN = 'return', 'Возвращение'
        CANCEL = 'cancel', 'Добровольная отмена'
        REENROLL = 'reenroll', 'Повторная стажировка'
        LEAVE_PROJECT = 'leave', 'Выход из одного проекта'
        ENROLL = 'enroll', 'Новая стажировка (регистрация в боте)'

    class Sales(models.TextChoices):
        PENDING = 'pending', 'Ждёт подтверждения продаж'
        CONFIRMED = 'confirmed', 'Оплата подтверждена'
        REJECTED = 'rejected', 'Продажи не подтвердили'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Ожидает решения'
        APPROVED = 'approved', 'Одобрена'
        REJECTED = 'rejected', 'Отклонена'

    class Source(models.TextChoices):
        BOT = 'bot', 'Telegram-бот'
        HEAD = 'head', 'Руководитель'

    intern = models.ForeignKey(Intern, on_delete=models.CASCADE, related_name='internship_requests',
                               verbose_name='Стажёр')
    kind = models.CharField('Вид', max_length=10, choices=Kind.choices)
    reason = models.TextField('Причина')
    status = models.CharField('Статус', max_length=10, choices=Status.choices, default=Status.PENDING,
                              db_index=True)
    source = models.CharField('Откуда', max_length=10, choices=Source.choices, default=Source.BOT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                     blank=True, related_name='+', verbose_name='Оформил')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='+', verbose_name='Решил')
    decided_at = models.DateTimeField('Решение', null=True, blank=True)
    decision_comment = models.TextField('Комментарий к решению', blank=True)
    # Что изменило одобрение — для истории и проверки
    left_projects = models.TextField('Вышел из проектов', blank=True)
    tasks_reset = models.PositiveIntegerField('Задач возвращено «К выполнению»', default=0)
    # Возвращение: проект, выбранный после одобрения (один раз)
    chosen_project = models.ForeignKey('projects.Project', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+', verbose_name='Выбранный проект')
    chosen_at = models.DateTimeField('Проект выбран', null=True, blank=True)
    # Повторная стажировка (UI-04B): сначала подтверждение продаж, потом руководитель
    sales_status = models.CharField('Подтверждение продаж', max_length=10, choices=Sales.choices, blank=True)
    sales_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='+', verbose_name='Подтвердил (продажи)')
    sales_at = models.DateTimeField('Продажи — когда', null=True, blank=True)
    sales_reference = models.CharField('Номер оплаты / договора', max_length=100, blank=True)
    sales_comment = models.TextField('Комментарий продаж', blank=True)
    # Выход выпускника из одного проекта (UI-04C)
    project = models.ForeignKey('projects.Project', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='+', verbose_name='Проект (выход)')

    class Meta:
        verbose_name = 'Заявка на заморозку / возвращение'
        verbose_name_plural = 'Заявки на заморозку / возвращение'
        ordering = ['-created_at']
        constraints = [
            # Одна ожидающая заявка каждого вида на человека — защита от дублей
            models.UniqueConstraint(fields=['intern', 'kind'], condition=models.Q(status='pending'),
                                    name='one_pending_internship_request'),
        ]

    def __str__(self) -> str:
        return f'{self.get_kind_display()}: {self.intern}'


class Internship(TimeStampedModel):
    """Период стажировки одного человека (UI-04A).

    Профиль один (Intern), периодов может быть несколько: первая и повторная
    стажировка. Заморозка и возвращение — внутри того же периода; новый
    период начинается только при повторном зачислении (UI-04B). Открытый
    период (без даты окончания) у человека один — это ограничение БД.
    Даты не угадываются: начало — дата из карточки или фактического
    вступления в проект; окончание ставит только явное событие.
    """

    class EndReason(models.TextChoices):
        FINISHED = 'finished', 'Завершил(а) стажировку'
        EXPELLED = 'expelled', 'Отчислен(а)'
        CANCELLED = 'cancelled', 'Отмена — ушёл(ла) самостоятельно'
        EMPLOYED = 'employed', 'Трудоустроен(а)'
        LEFT = 'left', 'Выбыл(а), причина не записана'
        PROMOTED = 'promoted', 'Повышен(а) до тимлида'

    class Source(models.TextChoices):
        JOIN = 'join', 'Вступление в проект'
        CARD = 'card', 'Дата из карточки'
        MANUAL = 'manual', 'Руководитель'

    intern = models.ForeignKey(Intern, on_delete=models.CASCADE, related_name='internships', verbose_name='Стажёр')
    number = models.PositiveSmallIntegerField('Стажировка по счёту', validators=[MinValueValidator(1)])
    started_on = models.DateField('Начало', null=True, blank=True)
    ended_on = models.DateField('Окончание', null=True, blank=True)
    end_reason = models.CharField('Причина окончания', max_length=10, choices=EndReason.choices, blank=True)
    source = models.CharField('Откуда дата начала', max_length=10, choices=Source.choices, default=Source.JOIN)
    note = models.CharField('Примечание', max_length=255, blank=True)
    # Первый подтверждённый выпуск в этом периоде (UI-04C): один на период,
    # сколько бы проектов ни закрылось — от него зависит сертификат
    graduated_on = models.DateField('Выпуск в этом периоде', null=True, blank=True)

    class Meta:
        verbose_name = 'Период стажировки'
        verbose_name_plural = 'Периоды стажировок'
        ordering = ['intern', 'number']
        constraints = [
            models.UniqueConstraint(fields=['intern', 'number'], name='internship_number_unique'),
            models.UniqueConstraint(fields=['intern'], condition=models.Q(ended_on__isnull=True, end_reason=''),
                                    name='one_open_internship'),
        ]

    def __str__(self) -> str:
        return f'{self.intern} — стажировка №{self.number}'

    @property
    def is_open(self) -> bool:
        return self.ended_on is None and not self.end_reason


class InternWarning(TimeStampedModel):
    """Предупреждение стажёру (UI-04A — хранение и подсчёт; решения — UI-04B)."""

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Действует'
        CANCELLED = 'cancelled', 'Снято'
        RESET = 'reset', 'Учтено — руководитель оставил'
        CLOSED = 'closed', 'Учтено при отчислении'

    intern = models.ForeignKey(Intern, on_delete=models.CASCADE, related_name='warnings', verbose_name='Стажёр')
    internship = models.ForeignKey('Internship', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='warnings', verbose_name='Период стажировки')
    case = models.ForeignKey('DisciplineCase', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='warnings', verbose_name='Решение по трём предупреждениям')
    project = models.ForeignKey('projects.Project', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='intern_warnings', verbose_name='Проект')
    reason = models.TextField('Причина')
    status = models.CharField('Статус', max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='+', verbose_name='Выдал')
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+', verbose_name='Снял')
    cancelled_at = models.DateTimeField('Снято', null=True, blank=True)
    cancel_reason = models.TextField('Почему снято', blank=True)

    class Meta:
        verbose_name = 'Предупреждение'
        verbose_name_plural = 'Предупреждения'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.intern}: {self.reason[:40]}'


class DisciplineCase(TimeStampedModel):
    """Три активных предупреждения → решение руководителя (UI-04B).

    Это не отчисление: руководитель выбирает «Отчислить» или «Оставить».
    Одно ожидающее решение на человека (ограничение БД).
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Ждёт решения'
        KEPT = 'kept', 'Оставлен'
        EXPELLED = 'expelled', 'Отчислен'

    intern = models.ForeignKey(Intern, on_delete=models.CASCADE, related_name='discipline_cases', verbose_name='Стажёр')
    internship = models.ForeignKey(Internship, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='discipline_cases', verbose_name='Период стажировки')
    status = models.CharField('Статус', max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name='Решил')
    decided_at = models.DateTimeField('Решение', null=True, blank=True)
    comment = models.TextField('Комментарий', blank=True)

    class Meta:
        verbose_name = 'Решение по трём предупреждениям'
        verbose_name_plural = 'Решения по трём предупреждениям'
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['intern'], condition=models.Q(status='pending'),
                                               name='one_pending_discipline_case')]

    def __str__(self) -> str:
        return f'{self.intern}: {self.get_status_display()}'


class InternshipCertificate(TimeStampedModel):
    """Сертификат — за стажировку в целом, не за проект (UI-04C).

    Один на период стажировки (OneToOne). Здесь только хранятся фактические
    данные (номер, дата, источник), когда они известны; автоматической
    выдачи нет — интеграция с кабинетом выпускника GeeksPro не запущена.
    """

    class Source(models.TextChoices):
        PORTAL = 'portal', 'Кабинет выпускника GeeksPro'
        MANUAL = 'manual', 'Внесено вручную'

    internship = models.OneToOneField(Internship, on_delete=models.CASCADE, related_name='certificate',
                                      verbose_name='Период стажировки')
    number = models.CharField('Номер', max_length=64, blank=True)
    issued_on = models.DateField('Дата выдачи', null=True, blank=True)
    source = models.CharField('Источник', max_length=10, choices=Source.choices, default=Source.MANUAL)
    note = models.CharField('Примечание', max_length=255, blank=True)

    class Meta:
        verbose_name = 'Сертификат стажировки'
        verbose_name_plural = 'Сертификаты стажировок'

    def __str__(self) -> str:
        return f'Сертификат {self.number or "без номера"}: {self.internship}'


class SalesConfirmation(TimeStampedModel):
    """Независимое подтверждение отдела продаж (UI-04C).

    Создаётся только через apps.interns.sales.record_sales_confirmation —
    границу интеграции с будущим Telegram-модулем продаж. Веб-интерфейса
    создания нет: руководитель не может подменить подтверждение продаж.
    """

    class Status(models.TextChoices):
        APPLIED = 'applied', 'Принято: заявка подтверждена'
        UNMATCHED = 'unmatched', 'Человек не найден — нужна сверка'
        NO_REQUEST = 'no_request', 'Нет заявки, ждущей продаж'
        REJECTED = 'rejected', 'Отклонено: неизвестный сотрудник продаж'
        MISMATCH = 'mismatch', 'Данные не совпали — нужна сверка'
        AMBIGUOUS = 'ambiguous', 'Несколько совпадений — ручная сверка'

    class Source(models.TextChoices):
        TELEGRAM = 'telegram', 'Telegram (отдел продаж)'

    source = models.CharField('Источник', max_length=10, choices=Source.choices, default=Source.TELEGRAM)
    external_id = models.CharField('Внешний id события', max_length=100, unique=True)
    agent_telegram_id = models.CharField('Telegram id сотрудника продаж', max_length=32)
    agent_name = models.CharField('Сотрудник продаж', max_length=255, blank=True)
    full_name = models.CharField('ФИО из продаж', max_length=255, blank=True)
    phone = models.CharField('Телефон из продаж', max_length=32, blank=True)
    reference = models.CharField('Номер оплаты / договора', max_length=100, blank=True)
    occurred_at = models.DateTimeField('Когда подтверждено')
    status = models.CharField('Результат', max_length=12, choices=Status.choices)
    direction = models.CharField('Направление из продаж', max_length=100, blank=True)
    city = models.CharField('Город из продаж', max_length=50, blank=True)
    detail = models.CharField('Пояснение', max_length=255, blank=True)
    intern = models.ForeignKey(Intern, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='sales_confirmations', verbose_name='Стажёр')
    request = models.ForeignKey(InternshipRequest, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='sales_confirmations', verbose_name='Заявка')

    class Meta:
        verbose_name = 'Подтверждение продаж'
        verbose_name_plural = 'Подтверждения продаж'
        ordering = ['-occurred_at']

    def __str__(self) -> str:
        return f'{self.full_name or self.phone}: {self.get_status_display()}'

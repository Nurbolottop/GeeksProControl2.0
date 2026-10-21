"""Бизнес-логика бота-выпускника — без обращений к Telegram API, чтобы
можно было полностью протестировать обычными django-тестами
(исключение — notify_resume_bank_decision: она шлёт сообщение, но сама
Telegram-логика в ней однострочная и мокается в тестах).

Диалог (реализован в apps/graduate_bot/bot.py): выпускник вводит ФИО,
подтверждает личность номером телефона, затем выбирает — продолжить
стажировку (сам выбирает свободный проект и добавляется в команду) или
уйти в банк резюме.
"""
import logging
import math
import re
from datetime import timedelta

from django.utils import timezone

from apps.interns.models import GraduateStatus, Intern, ResumeBankStatus
from apps.projects.models import Project, ProjectStatus
from apps.teams.models import TeamMember, TeamRole

logger = logging.getLogger(__name__)

# Сколько попыток подряд неверного телефона допускаем, прежде чем
# заблокировать проверку для этого человека (защита от подбора телефона
# по известному ФИО — см. lock_phone_verification).
PHONE_ATTEMPTS_LIMIT = 3
PHONE_LOCK_MINUTES = 30

# Чаты, где правила прочитали, но в аккаунт ещё не вошли: связать отметку
# не с чем, в карточку она уходит при входе. Живёт до перезапуска бота —
# в худшем случае человек прочитает правила ещё раз.
_rules_accepted: set[int] = set()


# Буквы, которые люди пишут и так и эдак: кыргызские ү/ө/ң набирают как
# у/о/н, «ё» — как «е», а латинские a/e/o/p/c/x/y часто проскакивают
# вместо кириллических в именах, набранных в спешке.
LETTER_FIXES = str.maketrans({
    'ё': 'е', 'ү': 'у', 'ө': 'о', 'ң': 'н', 'і': 'и', 'ъ': '', 'ь': '',
    'a': 'а', 'e': 'е', 'o': 'о', 'p': 'р', 'c': 'с', 'x': 'х', 'y': 'у',
    'k': 'к', 'm': 'м', 't': 'т', 'h': 'н', 'b': 'в',
})


def _name_words(value: str) -> list[str]:
    """Слова имени в сравнимом виде: без регистра, без лишних знаков и
    с приведением букв, которые пишут по-разному."""
    lowered = (value or '').lower().translate(LETTER_FIXES)
    return [word for word in re.split(r'[^0-9a-zа-я]+', lowered) if word]


def find_graduates(name: str, limit: int = 5) -> list[Intern]:
    """Выпускники с похожим ФИО.

    Люди пишут как придётся: «Иванов Иван», «Иван Иванов», одно имя, с
    опечаткой в регистре или через «ё». Поэтому сравниваем по словам и в
    любом порядке: подходит тот, у кого нашлись все введённые слова —
    целиком или как начало слова в карточке («Саид» → «Саидахмад»).
    """
    from apps.interns.services import graduated_interns

    return _search(graduated_interns(), name, limit)


def _search(people, name: str, limit: int) -> list[Intern]:
    """Поиск человека по тому, как его назвали.

    Сначала ищем строго: все введённые слова должны найтись. Если никого
    — ищем по любому слову: человек мог перепутать отчество или написать
    фамилию с ошибкой, и лучше показать ему похожих, чем тупик.
    """
    words = _name_words(name)
    if not words:
        return []

    def hits(intern: Intern) -> int:
        stored = _name_words(intern.full_name)
        return sum(
            1 for word in words
            if any(word == part or part.startswith(word) or word.startswith(part)
                   for part in stored)
        )

    scored = [(hits(person), person) for person in people]
    strict = [person for score, person in scored if score == len(words)]
    if strict:
        return strict[:limit]
    loose = sorted(
        (item for item in scored if item[0]), key=lambda item: -item[0],
    )
    return [person for _, person in loose][:limit]


def find_people(name: str, limit: int = 5) -> list[Intern]:
    """Поиск среди всех стажёров — чтобы отличить «такого нет» от
    «есть, но стажировка ещё идёт»."""
    people = list(
        Intern.objects.filter(is_archived=False).select_related('specialization'),
    )
    return _search(people, name, limit)


def _digits(value: str) -> str:
    return re.sub(r'\D', '', value or '')


PHONE_MATCH_MIN_DIGITS = 6


def phone_matches(intern: Intern, raw_phone: str) -> bool:
    """Сверка по последним цифрам — формат неважен (+996, 996, 0 или
    вообще без него, с пробелами/тире/скобками — всё, кроме цифр,
    отбрасывается).

    Длина сравнения — по короткой из двух записей, но не больше 9: если
    в карточке телефон когда-то занесли не полностью (меньше 9 цифр),
    человека всё равно можно проверить по тому, что есть, а не отказывать
    ему независимо от формата ввода. Ниже PHONE_MATCH_MIN_DIGITS не
    опускаемся — иначе проверка станет слишком легко угадываемой (уже
    есть отдельная блокировка после нескольких неверных попыток, см.
    lock_phone_verification, но короткий хвост дополнительно ослаблял бы
    её)."""
    entered = _digits(raw_phone)
    stored = _digits(intern.phone)
    tail = min(len(entered), len(stored), 9)
    if tail < PHONE_MATCH_MIN_DIGITS:
        return False
    return entered[-tail:] == stored[-tail:]


def save_phone_from_telegram(intern: Intern, phone: str, chat_id: int) -> None:
    """Записать номер, которым человек поделился из Telegram.

    У части выпускников телефона в базе нет вообще — проверить их по
    номеру нечем. Telegram отдаёт номер сам, подделать его в кнопке
    нельзя, поэтому такому входу мы доверяем, номер сохраняем в карточку
    и говорим руководителю: пусть знает, кто вошёл без сверки.
    """
    from django.urls import reverse

    from apps.audit.services import log as audit_log
    from apps.notifications.models import NotificationLevel
    from apps.notifications.services import notify

    intern.phone = phone
    intern.save(update_fields=['phone', 'updated_at'])
    remember_chat_id(intern, chat_id)
    audit_log(
        intern, 'Телефон получен из Telegram', new_value=phone,
        reason='бот-выпускник: в базе номера не было',
    )
    notify(
        f'Вход в бота без сверки: {intern.full_name}',
        level=NotificationLevel.WARNING,
        description=(
            f'Телефона в базе не было, человек поделился номером {phone} '
            'через Telegram. Проверьте, что это действительно он(а).'
        ),
        url=reverse('interns:detail', args=[intern.pk]),
        dedup_key=f'bot-login-no-phone:{intern.pk}',
    )


def is_phone_locked(intern: Intern) -> bool:
    """Проверку телефона для этого человека временно заблокировали —
    было слишком много неверных попыток подряд (см. lock_phone_verification)."""
    return bool(intern.phone_lock_until and intern.phone_lock_until > timezone.now())


def phone_lock_minutes_left(intern: Intern) -> int:
    if not intern.phone_lock_until:
        return 0
    remaining = intern.phone_lock_until - timezone.now()
    return max(0, math.ceil(remaining.total_seconds() / 60))


def lock_phone_verification(intern: Intern) -> None:
    """Слишком много неверных попыток телефона подряд — блокируем на
    PHONE_LOCK_MINUTES. Без этого перезапуск /start сбрасывал счётчик
    попыток (он живёт только в памяти процесса, а не в БД) — то есть
    ограничение в 3 попытки ничего не мешало обойти, просто начав диалог
    заново сколько угодно раз, подбирая телефон известного человека."""
    from apps.audit.services import log as audit_log

    intern.phone_lock_until = timezone.now() + timedelta(minutes=PHONE_LOCK_MINUTES)
    intern.save(update_fields=['phone_lock_until', 'updated_at'])
    audit_log(
        intern, 'Бот-выпускник: проверка телефона заблокирована',
        reason=f'{PHONE_ATTEMPTS_LIMIT} неверных попыток подряд, блокировка на {PHONE_LOCK_MINUTES} мин',
    )


def remember_chat_id(intern: Intern, chat_id: int) -> None:
    """Запоминаем chat_id — нужен, чтобы потом писать выпускнику
    проактивно (решение по банку резюме, распределение на проект), а не
    только в ответ. Заодно переносим в карточку отметку о правилах:
    до входа в аккаунт её некуда было записать."""
    from django.utils import timezone

    fields = []
    if intern.telegram_chat_id != chat_id:
        intern.telegram_chat_id = chat_id
        fields.append('telegram_chat_id')
    if chat_id in _rules_accepted and not intern.rules_accepted_at:
        intern.rules_accepted_at = timezone.now()
        fields.append('rules_accepted_at')
    if fields:
        intern.save(update_fields=fields + ['updated_at'])


def chat_taken_by_other(intern: Intern, chat_id: int) -> Intern | None:
    """Не занят ли этот Telegram другим выпускником.

    Один аккаунт на один чат: иначе с одного телефона можно было бы
    «войти» за разных людей и распорядиться чужим выбором.
    """
    other = Intern.objects.filter(telegram_chat_id=chat_id).exclude(pk=intern.pk).first()
    return other


def find_by_chat_id(chat_id: int) -> Intern | None:
    """Кого уже проверяли по телефону в этом чате — чтобы повторный
    /start не переспрашивал ФИО и телефон заново."""
    return Intern.objects.filter(telegram_chat_id=chat_id).order_by('-updated_at').first()


def accept_rules(chat_id: int) -> None:
    """Человек нажал «Я ознакомлен». Пока он не вошёл в аккаунт, знать,
    кто это, мы не можем — поэтому запоминаем сам чат, а в карточку
    отметка попадёт при входе (см. remember_chat_id)."""
    _rules_accepted.add(chat_id)


def rules_accepted(chat_id: int, intern: Intern | None = None) -> bool:
    """Читал ли этот человек правила — в текущем запуске бота или раньше."""
    if chat_id in _rules_accepted:
        return True
    return bool(intern and intern.rules_accepted_at)


def resolved_status_message(intern: Intern) -> str:
    """Текст для /start, когда человек уже проходил проверку раньше и
    уже сделал выбор (заявка в банк резюме или проект) — вместо того,
    чтобы заново гонять его через ФИО и телефон."""
    if intern.resume_bank_status == ResumeBankStatus.APPROVED:
        return (
            f'Здравствуйте, {intern.full_name}! Ваше резюме уже принято и '
            'опубликовано в банке резюме GeeksPro.'
        )
    if intern.resume_bank_status == ResumeBankStatus.REVISION:
        return (
            f'Здравствуйте, {intern.full_name}! Ваша заявка в банк резюме '
            f'отправлена на доработку.\n\nКомментарий: {intern.resume_bank_comment}'
        )
    if intern.resume_bank_status == ResumeBankStatus.PENDING:
        return (
            f'Здравствуйте, {intern.full_name}! Ваша заявка в банк резюме ещё '
            'на проверке у руководителя GeeksPro. Мы сообщим о результате здесь.'
        )
    if intern.graduate_status == GraduateStatus.WAITING:
        return (
            f'Здравствуйте, {intern.full_name}! Вы в базе ожидания '
            'следующего проекта — как появится место, вас добавят в '
            'команду и я напишу сюда.\n\nА пока можем сыграть 🙂'
        )
    return f'Здравствуйте, {intern.full_name}! Вы уже продолжаете стажировку в GeeksPro.'


def completed_projects(intern: Intern) -> list[TeamMember]:
    """Членства в командах завершённых проектов — для поздравительного
    сообщения («вы завершили проекты: ...»)."""
    return list(
        TeamMember.objects.filter(
            intern=intern, status=TeamMember.Status.LEFT,
            project__status=ProjectStatus.COMPLETED,
        )
        .select_related('project')
        .order_by('-project__actual_end_date', '-left_at'),
    )


def request_next_internship(intern: Intern, chat_id: int) -> bool:
    """Выпускник хочет продолжить, но свободных мест сейчас нет.

    Ставим его в очередь на ближайшую стажировку и говорим руководителю:
    иначе человек просто уходит из чата и теряется. Возвращаем False,
    если он уже в очереди, — повторное нажатие ничего не меняет.
    """
    from django.urls import reverse

    from apps.audit.services import log as audit_log
    from apps.notifications.models import NotificationLevel
    from apps.notifications.services import notify

    remember_chat_id(intern, chat_id)
    if intern.graduate_status == GraduateStatus.WAITING:
        return False
    intern.graduate_status = GraduateStatus.WAITING
    intern.save(update_fields=['graduate_status', 'updated_at'])
    audit_log(
        intern, 'Ждёт ближайшую стажировку',
        reason='бот-выпускник: свободных проектов не было',
    )
    notify(
        f'Ждёт стажировку: {intern.full_name}',
        level=NotificationLevel.INFO,
        description='Выпускник готов продолжать, но свободных проектов не было.',
        url=f"{reverse('interns:graduates')}?status={GraduateStatus.WAITING}",
        dedup_key=f'graduate-waiting:{intern.pk}',
    )
    return True


def submit_to_resume_bank(intern: Intern, chat_id: int) -> bool:
    """Выпускник подтвердил регистрацию на geeks.kg через бота — заявка
    уходит руководителю на проверку (apps.interns.views.resume_bank_approve/
    resume_bank_revise).

    Также снимаем ``graduate_status`` (как и распределение на проект) —
    без этого человек оставался бы в find_graduates() и мог заново пройти
    /start и повторно нажать подтверждение, откатив уже принятое решение
    руководителя обратно на «Ожидает проверки». Если резюме уже приняли
    (APPROVED) — ничего не меняем и возвращаем False, чтобы даже повторный
    вызов (второй клик по той же кнопке) не смог сбросить это решение.
    """
    from django.urls import reverse

    from apps.audit.services import log as audit_log
    from apps.notifications.models import NotificationLevel
    from apps.notifications.services import notify

    remember_chat_id(intern, chat_id)
    if intern.resume_bank_status == ResumeBankStatus.APPROVED:
        return False
    from apps.interns.models import InternStatus

    intern.in_resume_bank = True
    intern.resume_bank_status = ResumeBankStatus.PENDING
    intern.resume_bank_comment = ''
    intern.graduate_status = ''
    # Стажировка для него закончилась: в «свободных» на проект его ждать
    # больше не нужно, зато он доступен работодателям.
    intern.status = InternStatus.EMPLOYABLE
    intern.save(update_fields=[
        'in_resume_bank', 'resume_bank_status', 'resume_bank_comment',
        'graduate_status', 'status', 'updated_at',
    ])
    audit_log(intern, 'Заявка в банк резюме отправлена', reason='бот-выпускник')
    _put_into_reserve(intern)
    # intern=None — в общую ленту руководителя (apps.notifications), не в
    # чей-то личный портал: заявки в банк резюме проверяет только он сам.
    notify(
        f'Заявка в банк резюме: {intern.full_name}',
        level=NotificationLevel.INFO,
        description='Выпускник подтвердил регистрацию через бота — ждёт проверки.',
        url=reverse('interns:resume_bank'),
        dedup_key=f'resume-bank-submitted:{intern.pk}',
    )
    return True


def _put_into_reserve(intern: Intern) -> None:
    """Закончил стажировку — заводим карточку в резерве кадров.

    Раньше человек оседал только в банке резюме, а в резерв его
    приходилось заносить руками — и предлагать работодателям было некого.
    """
    from apps.reserve.models import ReserveCandidate
    from apps.reserve.services import candidate_from_intern

    if ReserveCandidate.objects.filter(intern=intern).exists():
        return
    candidate_from_intern(intern)


def notify_resume_bank_decision(intern: Intern, *, approved: bool, comment: str = '') -> None:
    """Сообщаем выпускнику решение руководителя по его заявке в банк
    резюме. Вызывается из админки (apps.interns.views), в отдельном от
    поллинга бота процессе — это нормально, отправка сообщения не
    требует владения long-poll соединением (конфликт 409 бывает только
    у getUpdates)."""
    if not intern.telegram_chat_id:
        return
    if approved:
        text = (
            f'🎉 Поздравляем, {intern.full_name}! Ваше резюме приняли — '
            'оно опубликовано в банке резюме GeeksPro.'
        )
    else:
        text = (
            'Ваше резюме нужно доработать.\n\n'
            f'Комментарий от руководителя:\n{comment}\n\n'
            'Когда исправите — сообщите руководителю GeeksPro.'
        )
    try:
        from apps.graduate_bot.bot import bot

        bot.send_message(intern.telegram_chat_id, text)
    except Exception:
        logger.exception('Не удалось отправить решение по банку резюме в Telegram (intern=%s)', intern.pk)


def invite_graduates(project: Project, interns) -> int:
    """Проект сдан — зовём выпускников в бота, не дожидаясь, пока они сами.

    Пишем только тем, кто уже общался с ботом: остальным Telegram писать
    не даёт, их зовёт руководитель ссылкой. Возвращаем, скольким ушло.
    """
    sent = 0
    for intern in interns:
        if not intern.telegram_chat_id:
            continue
        text = (
            f'🎉 {intern.full_name}, проект «{project.name}» сдан — '
            'поздравляем!\n\nТеперь выберите, что дальше: продолжить '
            'стажировку на новом проекте или закончить её и попасть в наш '
            'банк резюме. Нажмите /start.'
        )
        try:
            from apps.graduate_bot.bot import bot

            bot.send_message(intern.telegram_chat_id, text)
            sent += 1
        except Exception:
            logger.exception(
                'Не удалось позвать выпускника в бота (intern=%s)', intern.pk,
            )
    return sent


def notify_project_assigned(intern: Intern, project: Project) -> None:
    """Ждавшего распределили на проект — сообщаем ему об этом в Telegram.

    Обещание «как появится место, вас добавят» должно выполняться само:
    руководитель распределяет человека в системе, сообщение уходит без
    его участия.
    """
    if not intern.telegram_chat_id:
        return
    lead = team_lead_contact(project, intern)
    if lead:
        contact = lead.phone or lead.telegram or 'контакты уточните у руководителя'
        lead_line = f'\n\nВаш тимлид — {lead.full_name} ({contact}). Напишите ему(ей), чтобы договориться о старте.'
    else:
        lead_line = '\n\nТимлид пока не назначен — с вами свяжется руководитель GeeksPro.'
    text = (
        f'🎉 {intern.full_name}, для вас нашлось место! '
        f'Вы добавлены в проект «{project.name}».{lead_line}'
    )
    try:
        from apps.graduate_bot.bot import bot

        bot.send_message(intern.telegram_chat_id, text)
    except Exception:
        logger.exception(
            'Не удалось сообщить о распределении в Telegram (intern=%s)', intern.pk,
        )


def team_lead_contact(project: Project, intern: Intern) -> Intern | None:
    """Тимлид своего направления на этом проекте — кому писать после
    добавления в команду. У тимлида направление — по его собственной
    специализации (как apps.lead_portal.services.lead_own_role), не по
    роли TeamMember — она у всех тимлидов одна и та же, 'team_lead'."""
    from apps.teams.forms import ROLE_BY_SPECIALIZATION

    own_role = (
        ROLE_BY_SPECIALIZATION.get(intern.specialization.name)
        if intern.specialization else None
    )
    leads = (
        TeamMember.objects.filter(
            project=project, role=TeamRole.TEAM_LEAD, status=TeamMember.Status.ACTIVE,
        )
        .exclude(intern__isnull=True)
        .select_related('intern__specialization')
    )
    if own_role:
        for member in leads:
            spec = member.intern.specialization
            if spec and ROLE_BY_SPECIALIZATION.get(spec.name) == own_role:
                return member.intern
    first = leads.first()
    return first.intern if first else None


def bot_activity() -> list[Intern]:
    """Все, кто хоть раз подтвердил телефон в боте (успешно прошёл
    аутентификацию) — с их текущим выбором. Для админ-страницы
    «Бот-выпускник»: без неё не было видно, кто вообще пользовался
    ботом и чем это закончилось — после выбора человек просто пропадал
    из «Выпускников»."""
    interns = list(
        Intern.objects.exclude(telegram_chat_id__isnull=True)
        .select_related('specialization')
        .order_by('-updated_at'),
    )
    intern_ids = [i.pk for i in interns]
    latest_membership = {}
    for member in (
        TeamMember.objects.filter(
            intern_id__in=intern_ids, status=TeamMember.Status.ACTIVE,
        )
        .select_related('project').order_by('intern_id', '-joined_at')
    ):
        latest_membership.setdefault(member.intern_id, member)

    for intern in interns:
        if intern.graduate_status == GraduateStatus.WAITING:
            intern.bot_outcome = 'waiting'
        elif intern.graduate_status:
            intern.bot_outcome = 'pending'
        elif intern.resume_bank_status:
            intern.bot_outcome = 'resume_bank'
        else:
            intern.bot_outcome = 'continued'
            member = latest_membership.get(intern.pk)
            intern.bot_project = member.project if member else None
    return interns

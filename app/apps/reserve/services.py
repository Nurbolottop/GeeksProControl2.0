"""Бизнес-логика резерва кадров.

Здесь всё, что должно происходить автоматически: пересчёт рейтинга,
запись истории, смена статусов при рекомендациях. Views остаются
тонкими и не дублируют эту логику.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.reserve.models import (
    CandidatePool, CandidateStatus, EventKind, RecommendationStatus,
    ReserveCandidate, ReserveEvent, ReserveInvite, ReserveShareLink,
)

# Результат по рекомендации двигает и статус самого кандидата
STATUS_BY_RECOMMENDATION = {
    RecommendationStatus.SENT: CandidateStatus.PROPOSED,
    RecommendationStatus.REVIEW: CandidateStatus.PROPOSED,
    RecommendationStatus.INTERVIEW: CandidateStatus.INTERVIEW,
    RecommendationStatus.OFFER: CandidateStatus.OFFER,
    # «Нанят» компанией — ещё не подтверждённое трудоустройство (этап 6B):
    # подтверждает только руководитель (apps.reserve.employment)
    RecommendationStatus.HIRED: CandidateStatus.OFFER,
}


def log_event(candidate, kind, title, *, detail='', user=None) -> ReserveEvent:
    """История кандидата: пишем всегда, не удаляем никогда."""
    return ReserveEvent.objects.create(
        candidate=candidate, kind=kind, title=title, detail=detail,
        user=user if user and user.is_authenticated else None,
    )


def recalculate_rating(candidate: ReserveCandidate) -> None:
    """Общий рейтинг — среднее по выставленным критериям (шкала 1–10)."""
    scores = [
        getattr(candidate, field)
        for field, _ in ReserveCandidate.EVALUATION_CRITERIA
        if getattr(candidate, field) is not None
    ]
    if scores:
        total = sum(Decimal(str(s)) for s in scores)
        candidate.rating = round(total / len(scores), 2)
    else:
        candidate.rating = None
    candidate.save(update_fields=['rating', 'updated_at'])


def create_candidate(candidate: ReserveCandidate, user=None) -> ReserveCandidate:
    candidate.created_by = candidate.created_by or (
        user if user and user.is_authenticated else None
    )
    candidate.updated_by = user if user and user.is_authenticated else None
    candidate.save()
    log_event(
        candidate, EventKind.CREATED, 'Кандидат добавлен в резерв',
        detail=f'Статус: {candidate.get_status_display()}', user=user,
    )
    link_to_intern(candidate, user=user)
    return candidate


def update_candidate(candidate: ReserveCandidate, user=None, detail='') -> ReserveCandidate:
    candidate.updated_by = user if user and user.is_authenticated else None
    candidate.save()
    log_event(candidate, EventKind.UPDATED, 'Данные кандидата изменены',
              detail=detail, user=user)
    return candidate


def change_status(candidate: ReserveCandidate, status: str, *, comment='', user=None):
    """Смена статуса с записью в историю. Прежние записи остаются.

    «Трудоустройство подтверждено» так не ставится и не снимается — только
    apps.reserve.employment.confirm_employment.
    """
    if status == candidate.status:
        return candidate
    if CandidateStatus.EMPLOYED in (status, candidate.status):
        raise ValueError('Трудоустройство подтверждает руководитель отдельным действием.')
    was = candidate.get_status_display()
    candidate.status = status
    candidate.status_changed_at = timezone.now()
    candidate.updated_by = user if user and user.is_authenticated else None
    candidate.save(update_fields=[
        'status', 'status_changed_at', 'updated_by', 'updated_at',
    ])
    log_event(
        candidate, EventKind.STATUS,
        f'Статус: {was} → {candidate.get_status_display()}',
        detail=comment, user=user,
    )
    return candidate


def save_evaluation(candidate: ReserveCandidate, user=None) -> ReserveCandidate:
    """Сохранение внутренней оценки: пересчёт рейтинга + история."""
    candidate.updated_by = user if user and user.is_authenticated else None
    candidate.save()
    recalculate_rating(candidate)
    parts = [f'{label}: {value}' for label, value in candidate.scores if value]
    if candidate.decision:
        parts.append(f'Решение: {candidate.get_decision_display()}')
    if candidate.geekspro_level:
        parts.append(f'Уровень: {candidate.get_geekspro_level_display()}')
    log_event(
        candidate, EventKind.EVALUATED,
        f'Внутренняя оценка: {candidate.rating or "—"} / 10',
        detail='; '.join(parts), user=user,
    )
    return candidate


def issue_invite(
    candidate=None, *, user=None, ttl_days: int | None = 7, recipient='',
) -> ReserveInvite:
    """Новая ссылка на анкету.

    Без кандидата — общая ссылка (каждое заполнение = новая карточка).
    С кандидатом — ссылка на редактирование его анкеты; прежние ссылки
    на правку этой же карточки гасим, чтобы старая не гуляла по чатам.
    """
    if candidate is not None:
        ReserveInvite.objects.filter(
            candidate=candidate, is_active=True,
        ).update(is_active=False)
    invite = ReserveInvite.objects.create(
        candidate=candidate,
        recipient=recipient or (candidate.full_name if candidate else ''),
        created_by=user if user and user.is_authenticated else None,
        expires_at=timezone.now() + timedelta(days=ttl_days) if ttl_days else None,
    )
    if candidate is not None:
        log_event(
            candidate, EventKind.INVITED, 'Создана ссылка на редактирование анкеты',
            detail=invite.get_absolute_url(), user=user,
        )
    return invite


def issue_share_link(
    candidate, *, user=None, ttl_days: int | None = 30, recipient='',
    show_contacts=False,
) -> ReserveShareLink:
    """Ссылка на профиль кандидата для работодателя.

    Прежние ссылки не гасим: профиль могли отправить нескольким
    компаниям, и у каждой своя ссылка со своим сроком.
    """
    link = ReserveShareLink.objects.create(
        candidate=candidate,
        recipient=recipient,
        show_contacts=show_contacts,
        created_by=user if user and user.is_authenticated else None,
        expires_at=timezone.now() + timedelta(days=ttl_days) if ttl_days else None,
    )
    log_event(
        candidate, EventKind.SHARED,
        f'Создана ссылка на профиль{f" для «{recipient}»" if recipient else ""}',
        detail=' · '.join([
            link.get_absolute_url(),
            'с контактами' if show_contacts else 'без контактов',
        ]),
        user=user,
    )
    return link


def issue_share_collection(
    candidates, *, user=None, ttl_days: int | None = 30, recipient='',
    show_contacts=False,
) -> ReserveShareLink:
    """Одна ссылка на подборку кандидатов — вместо ссылки на каждого."""
    candidates = list(candidates)
    link = ReserveShareLink.objects.create(
        recipient=recipient,
        show_contacts=show_contacts,
        created_by=user if user and user.is_authenticated else None,
        expires_at=timezone.now() + timedelta(days=ttl_days) if ttl_days else None,
    )
    link.candidates.set(candidates)
    title = (
        f'Профиль добавлен в подборку{f" для «{recipient}»" if recipient else ""}'
    )
    detail = ' · '.join([
        link.get_absolute_url(),
        f'кандидатов в подборке: {len(candidates)}',
        'с контактами' if show_contacts else 'без контактов',
    ])
    for candidate in candidates:
        log_event(candidate, EventKind.SHARED, title, detail=detail, user=user)
    return link


def register_share_view(link: ReserveShareLink) -> None:
    """Профиль открыли по ссылке — считаем просмотры."""
    ReserveShareLink.objects.filter(pk=link.pk).update(
        views=models.F('views') + 1, viewed_at=timezone.now(),
    )


def accept_application(invite: ReserveInvite, form) -> ReserveCandidate:
    """Анкета отправлена по ссылке.

    По ссылке на редактирование обновляется та самая карточка. По общей
    ссылке каждое заполнение — отдельный человек; дубли ловим по
    телефону: если такой номер уже есть, обновляем его карточку.
    """
    from apps.reserve.forms import PUBLIC_FIELDS

    now = timezone.now()
    data = form.cleaned_data
    phone = (data.get('phone') or '').strip()

    if invite.is_edit_link:
        candidate = form.save(commit=False)
        is_new = False
    else:
        candidate = (
            ReserveCandidate.objects.filter(phone=phone).first() if phone else None
        )
        is_new = candidate is None
        if is_new:
            candidate = form.save(commit=False)
        else:
            for field in PUBLIC_FIELDS:
                value = data.get(field)
                # файлы затираем только если человек прислал новый
                if field in {'photo', 'resume_file'} and not value:
                    continue
                setattr(candidate, field, value)
            candidate.consent_given = True
    candidate.submitted_at = now
    candidate.consent_at = candidate.consent_at or now
    candidate.save()

    ReserveInvite.objects.filter(pk=invite.pk).update(
        submissions=models.F('submissions') + 1, used_at=now,
    )
    if is_new:
        log_event(candidate, EventKind.CREATED, 'Кандидат добавлен через анкету')
        log_event(
            candidate, EventKind.SUBMITTED, 'Кандидат заполнил анкету',
            detail='Согласие на передачу данных работодателям получено',
        )
    else:
        log_event(candidate, EventKind.SUBMITTED, 'Кандидат обновил свою анкету')
    link_to_intern(candidate)
    # Уже проверенного человека повторная правка анкеты не отбрасывает
    # назад по статусу — только новых и ещё не проверенных.
    if candidate.status in {CandidateStatus.NEW, CandidateStatus.REVIEW}:
        change_status(candidate, CandidateStatus.REVIEW, comment='Анкета отправлена кандидатом')
    return candidate


def add_recommendation(recommendation, user=None):
    """Рекомендация компании + автоматический перевод кандидата в статус."""
    recommendation.created_by = user if user and user.is_authenticated else None
    recommendation.save()
    candidate = recommendation.candidate
    log_event(
        candidate, EventKind.RECOMMENDED,
        f'Рекомендован(а) компании: {recommendation.company_title}',
        detail=' · '.join(filter(None, [
            recommendation.vacancy,
            f'{recommendation.sent_on:%d.%m.%Y}',
            recommendation.comment,
        ])),
        user=user,
    )
    target = STATUS_BY_RECOMMENDATION.get(recommendation.status)
    if target and candidate.status in {
        CandidateStatus.NEW, CandidateStatus.REVIEW, CandidateStatus.RESERVE,
    }:
        change_status(candidate, target, comment='Автоматически по рекомендации', user=user)
    return recommendation


def update_recommendation_status(recommendation, status: str, *, comment='', user=None):
    """Результат по рекомендации: пишем в историю и двигаем статус кандидата."""
    was = recommendation.get_status_display()
    recommendation.status = status
    if comment:
        recommendation.comment = comment
    recommendation.save(update_fields=['status', 'comment', 'updated_at'])
    candidate = recommendation.candidate
    log_event(
        candidate, EventKind.REC_STATUS,
        f'{recommendation.company_title}: {was} → {recommendation.get_status_display()}',
        detail=comment, user=user,
    )
    target = STATUS_BY_RECOMMENDATION.get(status)
    if candidate.status == CandidateStatus.EMPLOYED:
        return recommendation  # трудоустроенного рекомендации больше не двигают
    if target:
        change_status(candidate, target, comment='Автоматически по рекомендации', user=user)
    elif status in {
        RecommendationStatus.COMPANY_REJECT, RecommendationStatus.CANDIDATE_REJECT,
    } and candidate.status in {CandidateStatus.PROPOSED, CandidateStatus.INTERVIEW}:
        change_status(
            candidate, CandidateStatus.RESERVE,
            comment='Вернулся в резерв после отказа', user=user,
        )
    return recommendation


def candidate_from_intern(
    intern, user=None, *, comment_lead='', pool=CandidatePool.INTERN,
) -> ReserveCandidate:
    """Завести кандидата из карточки стажёра, не перепечатывая данные.

    Комментарий приходит от того, кто отправляет человека в резерв
    (обычно тимлида) — остальное кандидат заполняет сам. По умолчанию
    это стажёр: карточку заводят из его стажёрской карточки.
    """
    existing = ReserveCandidate.objects.filter(intern=intern).first()
    if existing is not None:
        return existing
    candidate = ReserveCandidate(
        intern=intern,
        pool=pool,
        comment_lead=comment_lead,
        full_name=intern.full_name,
        phone=intern.phone,
        email=intern.email,
        telegram=intern.telegram,
        city=intern.city,
        specialization=intern.specialization,
        training_group=intern.training_group,
        study_specialization=intern.specialization,
        study_end=intern.education_end_date,
    )
    return create_candidate(candidate, user)


# Группы направлений — по ним анкета решает, какие ссылки показывать
# (GitHub разработчику, Behance дизайнеру) и какие примеры навыков давать.
DIRECTION_KEYWORDS = [
    ('design', ('ux', 'ui', 'дизайн', 'design', 'graphic')),
    ('qa', ('qa', 'test', 'тест')),
    ('pm', ('pm', 'project', 'менедж', 'product')),
    ('dev', ('backend', 'frontend', 'mobile', 'devops', 'разраб', 'ios', 'android')),
]


def direction_group(name: str) -> str:
    """К какой группе относится направление: dev / design / qa / pm / other."""
    lowered = (name or '').lower()
    for group, keywords in DIRECTION_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return group
    return 'other'


def direction_groups_map() -> dict:
    """id направления → группа, для показа нужных вопросов в анкете."""
    from apps.training.models import Specialization

    return {
        str(spec.pk): direction_group(spec.name)
        for spec in Specialization.objects.all()
    }


def reorder_candidates(order: list[int], user=None) -> int:
    """Расставить кандидатов в заданном порядке: первый в списке — самый
    приоритетный. Тем, кого в списке нет, приоритеты не трогаем."""
    candidates = ReserveCandidate.objects.in_bulk(order)
    top = len(order)
    changed = []
    for index, pk in enumerate(order):
        candidate = candidates.get(pk)
        if candidate is None:
            continue
        value = top - index
        if candidate.priority != value:
            candidate.priority = value
            changed.append(candidate)
    if changed:
        ReserveCandidate.objects.bulk_update(changed, ['priority'])
    return len(changed)


# --- Связь резерва с карточками людей ----------------------------------------
# Большая часть резерва — наши же бывшие и текущие тимлиды. Карточка в
# резерве и карточка человека — это один человек: связываем их, чтобы в
# резюме подтягивались проекты у нас, а сам человек мог править своё
# резюме из портала без ссылок.

def _digits(value: str) -> str:
    """Телефон без кода страны и оформления: +996 (555) 12-34-56 → 555123456."""
    import re

    return re.sub(r'\D', '', value or '')[-9:]


def _handle(value: str) -> str:
    return (value or '').strip().lower().lstrip('@')


def find_intern_for(candidate: ReserveCandidate):
    """Карточка человека с тем же телефоном, почтой или telegram.

    Совпадение должно быть однозначным и карточка — ещё ни к кому не
    привязанной: сомнительные случаи не связываем, их решает человек.
    """
    from apps.interns.models import Intern

    phone = _digits(candidate.phone)
    email = (candidate.email or '').strip().lower()
    handle = _handle(candidate.telegram)
    if not (phone or email or handle):
        return None
    found = []
    for person in Intern.objects.filter(reserve_card__isnull=True):
        if (
            (phone and len(phone) >= 9 and _digits(person.phone) == phone)
            or (email and (person.email or '').strip().lower() == email)
            or (handle and _handle(person.telegram) == handle)
        ):
            found.append(person)
    return found[0] if len(found) == 1 else None


def link_to_intern(candidate: ReserveCandidate, user=None):
    """Привязать кандидата к карточке человека, если она однозначно нашлась."""
    if candidate.intern_id:
        return candidate.intern
    person = find_intern_for(candidate)
    if person is None:
        return None
    candidate.intern = person
    candidate.save(update_fields=['intern', 'updated_at'])
    log_event(
        candidate, EventKind.UPDATED, 'Связан(а) с карточкой в базе людей',
        detail=f'{person.full_name}: проекты у нас подтягиваются в резюме',
        user=user,
    )
    return person


def reserve_card_of(user):
    """Карточка в резерве того, кто вошёл в портал (через его карточку человека)."""
    person = getattr(user, 'intern_profile', None)
    if person is None:
        return None
    return ReserveCandidate.objects.filter(intern=person, is_archived=False).first()


def save_own_resume(candidate: ReserveCandidate, form, user) -> ReserveCandidate:
    """Человек сам обновил резюме из портала — без ссылки, под своим логином."""
    candidate = form.save(commit=False)
    now = timezone.now()
    candidate.submitted_at = now
    candidate.consent_at = candidate.consent_at or now
    candidate.updated_by = user
    candidate.save()
    changed = ', '.join(form.changed_data)
    log_event(
        candidate, EventKind.SUBMITTED, 'Кандидат обновил резюме в своём портале',
        detail=changed, user=user,
    )
    return candidate


# --- Тимлиды автоматически в резерве -----------------------------------------
# Каждый, кто у нас тимлид, сразу попадает в резерв кадров — руками никто
# не добавляет. Если в резерве уже лежит его анкета без привязки, берём её,
# чтобы не плодить дубль; иначе заводим карточку из его данных.

def find_candidate_for(intern):
    """Непривязанная анкета в резерве с тем же телефоном, почтой или telegram."""
    phone = _digits(intern.phone)
    email = (intern.email or '').strip().lower()
    handle = _handle(intern.telegram)
    if not (phone or email or handle):
        return None
    found = [
        candidate for candidate in ReserveCandidate.objects.filter(intern__isnull=True)
        if (phone and len(phone) >= 9 and _digits(candidate.phone) == phone)
        or (email and (candidate.email or '').strip().lower() == email)
        or (handle and _handle(candidate.telegram) == handle)
    ]
    return found[0] if len(found) == 1 else None


def ensure_lead_in_reserve(intern, user=None) -> ReserveCandidate:
    """Тимлид в резерве: существующая карточка, найденная анкета или новая."""
    existing = ReserveCandidate.objects.filter(intern=intern).first()
    if existing is not None:
        return existing
    candidate = find_candidate_for(intern)
    if candidate is not None:
        candidate.intern = intern
        candidate.save(update_fields=['intern', 'updated_at'])
        log_event(
            candidate, EventKind.UPDATED, 'Тимлид GeeksPro — анкета связана с карточкой',
            detail=f'{intern.full_name}: проекты у нас подтягиваются в резюме',
            user=user,
        )
        return candidate
    # Тимлид — сотрудник, а не стажёр: в таблицу стажёров он не идёт.
    candidate = candidate_from_intern(intern, user, pool=CandidatePool.STAFF)
    log_event(
        candidate, EventKind.UPDATED, 'Тимлид GeeksPro — добавлен в резерв автоматически',
        detail='Резюме тимлид дополняет сам в своём портале', user=user,
    )
    change_status(
        candidate, CandidateStatus.REVIEW,
        comment='Тимлид добавлен в резерв автоматически', user=user,
    )
    return candidate



def accept_external(form) -> ReserveCandidate:
    """Заявка внешнего кандидата: на рассмотрение, без привязки к стажёру.

    Тот же телефон за последние 30 дней у кандидата без профиля — это
    повтор: данные не перезаписываем, пишем событие в историю.
    """
    from django.db import transaction

    phone = _digits(form.cleaned_data.get('phone', ''))
    since = timezone.now() - timedelta(days=30)
    with transaction.atomic():
        for existing in ReserveCandidate.objects.select_for_update().filter(intern__isnull=True, created_at__gte=since):
            if phone and _digits(existing.phone)[-9:] == phone[-9:]:
                log_event(existing, EventKind.SUBMITTED, 'Повторная заявка внешнего кандидата',
                          detail=form.cleaned_data.get('work_experience', '')[:500])
                return existing
        candidate = form.save(commit=False)
        candidate.pool = CandidatePool.STAFF
        candidate.status = CandidateStatus.REVIEW
        candidate.submitted_at = candidate.consent_at = timezone.now()
        candidate.save()
        log_event(candidate, EventKind.SUBMITTED, 'Заявка внешнего кандидата (публичная форма)',
                  detail='Кандидатура на рассмотрении; с профилем стажёра не связана')
    return candidate

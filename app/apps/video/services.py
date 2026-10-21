"""Видеособрания (этап 9A): права, создание, перенос, отмена, ручная отметка
проведения, персональные ссылки и приглашения.

Права (проверяются здесь):
* обязательное / дополнительное собрание направления — создаёт и ведёт только
  активный тимлид этого направления в этом проекте (ведущий — он сам);
* встреча ПМ с тимлидами — активный ПМ проекта; участники — тимлиды проекта;
  она не обязательная и в норму тимлидов не входит;
* руководитель видит всё и может исправлять посещаемость и отмечать проведение;
* ПМ видит собрания своих проектов (только чтение направлений);
* проект закрыт / отменён — новых встреч нет.
Встреча хранит время и статус сама — независимо от видеосервиса.
"""
from __future__ import annotations

import datetime

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.projects.models import ProjectStatus
from apps.teams.models import TeamMember, TeamRole
from apps.video.models import MeetingParticipant, VideoMeeting

WRITABLE = (ProjectStatus.ACTIVE, ProjectStatus.PAUSED)
JOIN_EARLY = datetime.timedelta(minutes=60)
JOIN_LATE = datetime.timedelta(minutes=120)


class MeetingError(ValidationError):
    pass


def _is_head(user):
    from apps.accounts.access import has_full_access

    return has_full_access(user)


def _profile(user):
    return getattr(user, 'intern_profile', None) if user and user.is_authenticated else None


def lead_directions(intern, project) -> list[str]:
    from apps.reports.weekly import lead_directions as directions

    return directions(intern, project) if intern else []


def is_active_pm(intern, project) -> bool:
    return bool(intern) and TeamMember.objects.filter(project=project, intern=intern, role=TeamRole.PROJECT_MANAGER,
                                                      status=TeamMember.Status.ACTIVE).exists()


def direction_members(project, direction):
    """Активные стажёры направления в проекте (тимлиды и сотрудники — нет)."""
    from apps.teams.selectors import staff_intern_ids

    return (TeamMember.objects.filter(project=project, role=direction, status=TeamMember.Status.ACTIVE,
                                      intern__isnull=False, intern__is_archived=False)
            .exclude(intern_id__in=staff_intern_ids()).select_related('intern').order_by('intern__full_name'))


def project_leads(project):
    return (TeamMember.objects.filter(project=project, role=TeamRole.TEAM_LEAD, status=TeamMember.Status.ACTIVE,
                                      intern__isnull=False).select_related('intern').order_by('intern__full_name'))


def can_view(user, meeting) -> bool:
    intern = _profile(user)
    if _is_head(user):
        return True
    if intern is None:
        return False
    if is_active_pm(intern, meeting.project):
        return True
    if meeting.kind == VideoMeeting.Kind.PM_LEADS:
        return meeting.host_id == intern.pk or meeting.participants.filter(intern=intern).exists()
    return meeting.direction in lead_directions(intern, meeting.project)


def can_manage(user, meeting) -> bool:
    """Ведёт встречу: тимлид направления (или ПМ — свою встречу с тимлидами)."""
    intern = _profile(user)
    if intern is None or meeting.project.status not in WRITABLE:
        return False
    if meeting.kind == VideoMeeting.Kind.PM_LEADS:
        return meeting.host_id == intern.pk and is_active_pm(intern, meeting.project)
    return meeting.direction in lead_directions(intern, meeting.project)


def can_correct(user, meeting) -> bool:
    """Исправлять посещаемость: руководитель или тимлид этого направления."""
    if _is_head(user):
        return True
    intern = _profile(user)
    return (meeting.is_direction_meeting and intern is not None
            and meeting.direction in lead_directions(intern, meeting.project))


def _week_start(moment) -> datetime.date:
    day = timezone.localtime(moment).date()
    return day - datetime.timedelta(days=day.weekday())


def create_meeting(user, project, *, kind, topic, starts_at, duration_minutes=60, direction='', intern_ids=()):
    from apps.video.providers import get_provider, unavailable_reason

    if reason := unavailable_reason():
        raise MeetingError(reason)
    host = _profile(user)
    topic = (topic or '').strip()[:255]
    if not topic:
        raise MeetingError('Укажите тему собрания.')
    if kind not in VideoMeeting.Kind.values:
        raise MeetingError('Неизвестный вид встречи.')
    if starts_at is None or timezone.is_naive(starts_at):
        raise MeetingError('Укажите дату и время.')
    if not 15 <= int(duration_minutes or 0) <= 240:
        raise MeetingError('Длительность — от 15 до 240 минут.')
    if project.status not in WRITABLE:
        raise MeetingError('Проект закрыт — новые собрания не назначаются.')
    if kind == VideoMeeting.Kind.PM_LEADS:
        if not is_active_pm(host, project):
            raise PermissionDenied('Встречу с тимлидами назначает ПМ этого проекта.')
        direction = ''
        allowed = {m.intern_id: m for m in project_leads(project)}
    else:
        if direction not in lead_directions(host, project):
            raise PermissionDenied('Собрание направления назначает тимлид этого направления в проекте.')
        allowed = {m.intern_id: m for m in direction_members(project, direction)}
    ids = [int(x) for x in intern_ids if str(x).isdigit()]
    chosen = [allowed[i] for i in ids if i in allowed]
    if len(chosen) != len(set(ids)):
        raise MeetingError('Можно пригласить только действующих участников этого направления проекта.')
    if not chosen:
        raise MeetingError('Выберите участников.')
    with transaction.atomic():
        meeting = VideoMeeting.objects.create(
            project=project, direction=direction, kind=kind, topic=topic, starts_at=starts_at,
            duration_minutes=int(duration_minutes), week_start=_week_start(starts_at), host=host, created_by=user,
            provider=get_provider().name)
        get_provider().prepare_room(meeting)
        for member in chosen:
            MeetingParticipant.objects.create(meeting=meeting, intern=member.intern,
                                              person_name=member.intern.full_name, role=member.role)
        _log(meeting, 'Собрание назначено', user, new=f'{meeting.get_kind_display()} · {topic}')
        transaction.on_commit(lambda: send_invitations(meeting))
    return meeting


def _log(meeting, action, user, old='', new='', reason=''):
    from apps.audit.services import log as audit_log

    audit_log(meeting, action, user=user, old_value=old, new_value=new, reason=reason)


def reschedule(meeting, user, starts_at):
    with transaction.atomic():
        meeting = VideoMeeting.objects.select_for_update().get(pk=meeting.pk)
        if not can_manage(user, meeting):
            raise PermissionDenied('Переносит ведущий собрания.')
        if meeting.status != VideoMeeting.Status.PLANNED or meeting.events.exists():
            raise MeetingError('Перенести можно только не начатое запланированное собрание.')
        old = timezone.localtime(meeting.starts_at)
        meeting.starts_at, meeting.week_start = starts_at, _week_start(starts_at)
        meeting.save(update_fields=['starts_at', 'week_start', 'updated_at'])
        _log(meeting, 'Собрание перенесено', user, old=f'{old:%d.%m.%Y %H:%M}',
             new=f'{timezone.localtime(starts_at):%d.%m.%Y %H:%M}')
    return meeting


def cancel(meeting, user, reason):
    reason = (reason or '').strip()
    if not reason:
        raise MeetingError('Укажите причину отмены.')
    with transaction.atomic():
        meeting = VideoMeeting.objects.select_for_update().get(pk=meeting.pk)
        if not (can_manage(user, meeting) or _is_head(user)):
            raise PermissionDenied('Отменяет ведущий собрания или руководитель.')
        if meeting.status != VideoMeeting.Status.PLANNED:
            raise MeetingError('Отменить можно только запланированное собрание.')
        meeting.status, meeting.cancelled_reason = VideoMeeting.Status.CANCELLED, reason
        meeting.save(update_fields=['status', 'cancelled_reason', 'updated_at'])
        _log(meeting, 'Собрание отменено', user, reason=reason)
    return meeting


def mark_held(meeting, user, note):
    """Ручная отметка «проведено» (например, сбой видеосервиса) — с причиной;
    в отчётах видно, что проведение подтверждено вручную, а не провайдером."""
    note = (note or '').strip()
    if not note:
        raise MeetingError('Укажите, почему проведение отмечается вручную.')
    with transaction.atomic():
        meeting = VideoMeeting.objects.select_for_update().get(pk=meeting.pk)
        if not (can_manage(user, meeting) or _is_head(user)):
            raise PermissionDenied('Отмечает ведущий собрания или руководитель.')
        if meeting.status != VideoMeeting.Status.PLANNED:
            raise MeetingError('Собрание уже проведено или отменено.')
        if meeting.starts_at > timezone.now():
            raise MeetingError('Собрание ещё не началось.')
        meeting.status, meeting.held_source = VideoMeeting.Status.HELD, VideoMeeting.HeldSource.MANUAL
        meeting.held_at, meeting.held_by, meeting.held_note = timezone.now(), user, note
        meeting.save(update_fields=['status', 'held_source', 'held_at', 'held_by', 'held_note', 'updated_at'])
        _log(meeting, 'Проведение отмечено вручную', user, reason=note)
    return meeting


# ================================================================ ссылки

def participant_still_allowed(participant) -> bool:
    meeting = participant.meeting
    if participant.removed_at or meeting.status == VideoMeeting.Status.CANCELLED:
        return False
    if meeting.kind == VideoMeeting.Kind.PM_LEADS:
        return project_leads(meeting.project).filter(intern=participant.intern).exists()
    return direction_members(meeting.project, meeting.direction).filter(intern=participant.intern).exists()


GRANT_TTL = datetime.timedelta(minutes=5)


def _check_window(participant, now):
    meeting = participant.meeting
    end = meeting.starts_at + datetime.timedelta(minutes=meeting.duration_minutes) + JOIN_LATE
    if not participant_still_allowed(participant):
        raise PermissionDenied('Вы больше не участник этого собрания.')
    if now < meeting.starts_at - JOIN_EARLY or now > end:
        raise MeetingError('Вход открыт за час до начала и до двух часов после окончания.')


def participant_for_token(token: str, now=None):
    """Ссылка из приглашения (этап 10A): сама по себе JWT НЕ выдаёт — только
    показывает, что вход подтверждается кнопкой в Telegram-боте."""
    participant = (MeetingParticipant.objects.select_related('meeting__project', 'intern')
                   .filter(join_token=token).first())
    if participant is None:
        raise PermissionDenied('Ссылка недействительна.')
    now = now or timezone.now()
    _check_window(participant, now)
    if participant.link_opened_at is None:
        MeetingParticipant.objects.filter(pk=participant.pk).update(link_opened_at=now)
    return participant


def issue_entry(participant_pk, telegram_chat_id, now=None) -> str:
    """Кнопка «Войти на собрание» в боте: только из подтверждённого Telegram
    этого участника. Возвращает одноразовый код (5 минут); в БД — только хеш."""
    import hashlib
    import secrets

    from apps.video.models import EntryGrant

    now = now or timezone.now()
    participant = (MeetingParticipant.objects.select_related('meeting__project', 'intern')
                   .filter(pk=participant_pk).first())
    if participant is None or not telegram_chat_id or participant.intern.telegram_chat_id != int(telegram_chat_id):
        raise PermissionDenied('Вход подтверждается только из Telegram участника.')
    _check_window(participant, now)
    code = secrets.token_urlsafe(24)
    EntryGrant.objects.create(participant=participant, code_hash=hashlib.sha256(code.encode()).hexdigest(),
                              telegram_chat_id=int(telegram_chat_id), expires_at=now + GRANT_TTL)
    return code


def redeem_entry(code: str, now=None) -> str:
    """Одноразовый код → URL видеосервиса с JWT (userId — наш случайный id).
    Повтор, просроченный код или отозванное участие — отказ."""
    import hashlib

    from apps.video.models import EntryGrant
    from apps.video.providers import get_provider

    now = now or timezone.now()
    with transaction.atomic():
        grant = (EntryGrant.objects.select_for_update().select_related('participant__meeting__project',
                                                                      'participant__intern')
                 .filter(code_hash=hashlib.sha256((code or '').encode()).hexdigest()).first())
        if grant is None:
            raise PermissionDenied('Код входа недействителен.')
        if grant.used_at is not None:
            raise PermissionDenied('Код входа уже использован — нажмите кнопку в боте ещё раз.')
        if grant.expires_at < now:
            raise PermissionDenied('Код входа устарел — нажмите кнопку в боте ещё раз.')
        participant = grant.participant
        if participant.intern.telegram_chat_id != grant.telegram_chat_id:
            raise PermissionDenied('Telegram участника изменился — подтвердите вход заново.')
        _check_window(participant, now)
        grant.used_at = now
        grant.save(update_fields=['used_at'])
    meeting = participant.meeting
    return get_provider(meeting.provider).join_url(meeting, user_id=participant.provider_user_id,
                                                   name=participant.person_name)


def host_join_url(meeting, user) -> str:
    from apps.video.providers import get_provider

    if _profile(user) is None or meeting.host_id != _profile(user).pk or meeting.status == VideoMeeting.Status.CANCELLED:
        raise PermissionDenied('Ссылка ведущего — только у ведущего собрания.')
    return get_provider(meeting.provider).join_url(meeting, user_id=meeting.host_user_id,
                                                   name=meeting.host.full_name, moderator=True)


def invite_link(participant) -> str:
    return f'{settings.SITE_URL}{reverse("video:join", args=[participant.join_token])}'


def entry_link(code) -> str:
    return f'{settings.SITE_URL}{reverse("video:enter", args=[code])}'


def send_invitations(meeting) -> int:
    """Персональные приглашения через очередь Telegram (GuardedTeleBot).
    Только тем, кто сейчас в направлении; без Telegram — «нет Telegram»."""
    from apps.tgbot import notify

    meeting = VideoMeeting.objects.select_related('project').get(pk=meeting.pk)
    when = timezone.localtime(meeting.starts_at)
    label = dict(TeamRole.choices).get(meeting.direction, 'тимлиды проекта') if meeting.direction else 'тимлиды проекта'
    count = 0
    for participant in meeting.participants.select_related('intern').filter(removed_at__isnull=True):
        if not participant_still_allowed(participant):
            continue
        text = (f'Собрание: {meeting.topic}\nПроект: {meeting.project.name}\nНаправление: {label}\n'
                f'Когда: {when:%d.%m.%Y %H:%M} (Бишкек)\n'
                f'{"Обязательное собрание" if meeting.kind == VideoMeeting.Kind.REQUIRED else meeting.get_kind_display()}'
                '\n\nВход — кнопкой ниже, за час до начала: бот выдаст одноразовую ссылку (5 минут). '
                'Пересланное сообщение войти не позволит.')
        notify.to_intern(participant.intern, 'meeting_invite', text, with_menu=False,
                         dedup_key=f'meeting-invite:{participant.pk}:{int(meeting.starts_at.timestamp())}',
                         markup=notify.menus.inline([[('Войти на собрание', f'meetjoin:{participant.pk}')]]))
        MeetingParticipant.objects.filter(pk=participant.pk).update(invited_at=timezone.now())
        count += 1
    return count

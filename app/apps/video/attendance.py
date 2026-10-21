"""Посещаемость видеособраний (этап 9B).

Автоматически — только по событиям провайдера с подтверждённой личностью
(наш userId участника этой встречи). Повторные подключения и второе
устройство — тот же человек: сессии группируются по participantId
провайдера (устройство), человек — по нашему userId. Длительность —
только если у сессии есть время выхода (или провайдер сообщил закрытие
комнаты); иначе «нет данных», не выдумываем. Порог «сколько минут нужно
быть» не применяется — нет бизнес-решения.

Значения для человека:
* auto_present — подтверждено автоматически;
* auto_absent — по данным видеосервиса не подключался (данные о встрече есть);
* no_data — достоверных сведений нет (событий о встрече нет вовсе);
* manual_* — исправлено вручную (последнее исправление; события не меняются).
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.video import services
from apps.video.models import AttendanceCorrection, MeetingParticipant, ProviderEvent, VideoMeeting

LABELS = {
    'auto_present': 'Подтверждено автоматически', 'auto_absent': 'Не подключался (по данным видеосервиса)',
    'no_data': 'Нет достоверных сведений', 'manual_present': 'Исправлено вручную: присутствовал(а)',
    'manual_absent': 'Исправлено вручную: отсутствовал(а)', 'manual_excused': 'Исправлено вручную: уважительная',
}
TONES = {'auto_present': 'success', 'manual_present': 'success', 'auto_absent': 'danger', 'manual_absent': 'danger',
         'manual_excused': 'info', 'no_data': 'neutral'}
PRESENT = ('auto_present', 'manual_present')


class AttendanceError(ValidationError):
    pass


@dataclass
class Presence:
    participant: MeetingParticipant
    auto: str
    value: str
    joins: int = 0
    devices: int = 0
    first_join: datetime.datetime | None = None
    minutes: int | None = None
    parallel: bool = False
    corrections: list = field(default_factory=list)

    @property
    def label(self):
        return LABELS[self.value]

    @property
    def tone(self):
        return TONES[self.value]

    @property
    def auto_label(self):
        return LABELS[self.auto].lower()

    @property
    def present(self):
        return self.value in PRESENT


def coverage(meeting) -> bool:
    """Есть ли от видеосервиса хоть какие-то сведения об этой встрече."""
    return meeting.room_started_at is not None or meeting.events.exists()


def _sessions(events, room_end):
    """[(начало, конец|None, устройство)] — по participantId провайдера."""
    open_by_device, sessions = {}, []
    for e in events:
        device = e.provider_participant_id or e.provider_user_id
        if e.event_type == 'PARTICIPANT_JOINED':
            if device in open_by_device:
                sessions.append((open_by_device.pop(device), None, device))  # повторный вход без выхода
            open_by_device[device] = e.occurred_at
        elif e.event_type == 'PARTICIPANT_LEFT' and device in open_by_device:
            sessions.append((open_by_device.pop(device), e.occurred_at, device))
    for device, start in open_by_device.items():
        sessions.append((start, room_end, device))
    return sessions


def _minutes(sessions):
    if not sessions or any(end is None for _s, end, _d in sessions):
        return None  # нет времени выхода — длительность не выдумываем
    spans = sorted((s, e) for s, e, _d in sessions)
    total, current_start, current_end = 0.0, spans[0][0], spans[0][1]
    for start, end in spans[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += (current_end - current_start).total_seconds()
            current_start, current_end = start, end
    total += (current_end - current_start).total_seconds()
    return int(total // 60)


def _parallel(sessions) -> bool:
    for i, (s1, e1, d1) in enumerate(sessions):
        for s2, e2, d2 in sessions[i + 1:]:
            if d1 != d2 and s1 < (e2 or s1 + datetime.timedelta(days=1)) and s2 < (e1 or s2 + datetime.timedelta(days=1)):
                return True
    return False


def compute_many(meetings) -> dict:
    """Посещаемость для многих встреч за 4 запроса (этап 10B: без N+1 на Dashboard)."""
    meetings = list(meetings)
    ids = [m.pk for m in meetings]
    participants, events, corrections = {}, {}, {}
    for p in MeetingParticipant.objects.filter(meeting_id__in=ids).select_related('intern'):
        participants.setdefault(p.meeting_id, []).append(p)
    for e in ProviderEvent.objects.filter(meeting_id__in=ids, verification=ProviderEvent.Verification.VERIFIED) \
            .order_by('occurred_at', 'pk'):
        events.setdefault(e.meeting_id, []).append(e)
    for c in AttendanceCorrection.objects.filter(participant__meeting_id__in=ids).select_related('changed_by'):
        corrections.setdefault(c.participant_id, []).append(c)
    with_events = set(ProviderEvent.objects.filter(meeting_id__in=ids).values_list('meeting_id', flat=True).distinct())
    return {m.pk: compute(m, participants.get(m.pk, []), _events=events.get(m.pk, []), _corrections=corrections,
                          _covered=m.room_started_at is not None or m.pk in with_events) for m in meetings}


def compute(meeting, participants=None, *, _events=None, _corrections=None, _covered=None) -> list[Presence]:
    participants = participants if participants is not None else list(meeting.participants.select_related('intern'))
    events = _events if _events is not None else list(
        meeting.events.filter(verification=ProviderEvent.Verification.VERIFIED).order_by('occurred_at', 'pk'))
    corrections = _corrections
    if corrections is None:
        corrections = {}
        for c in AttendanceCorrection.objects.filter(participant__meeting=meeting).select_related('changed_by'):
            corrections.setdefault(c.participant_id, []).append(c)
    covered = coverage(meeting) if _covered is None else _covered
    over = meeting.room_ended_at is not None or timezone.now() > (
        meeting.starts_at + datetime.timedelta(minutes=meeting.duration_minutes))
    result = []
    for p in participants:
        own = [e for e in events if e.participant_id == p.pk]
        sessions = _sessions(own, meeting.room_ended_at)
        joins = [e for e in own if e.event_type == 'PARTICIPANT_JOINED']
        if joins:
            auto = 'auto_present'
        elif covered and over and meeting.status != VideoMeeting.Status.CANCELLED:
            auto = 'auto_absent'
        else:
            auto = 'no_data'
        history = corrections.get(p.pk, [])
        value = f'manual_{history[-1].new_value}' if history else auto
        result.append(Presence(participant=p, auto=auto, value=value, joins=len(joins),
                               devices=len({s[2] for s in sessions}), first_join=joins[0].occurred_at if joins else None,
                               minutes=_minutes(sessions), parallel=_parallel(sessions), corrections=history))
    return result


def identity_issues(meeting) -> dict:
    unverified = meeting.events.filter(verification=ProviderEvent.Verification.UNVERIFIED,
                                       event_type='PARTICIPANT_JOINED')
    return {'unverified': unverified.values('provider_participant_id').distinct().count(),
            'unverified_names': sorted({n for n in unverified.values_list('display_name', flat=True) if n})[:10]}


def correct(participant, user, value, reason) -> AttendanceCorrection:
    from apps.audit.services import log as audit_log

    reason = (reason or '').strip()
    if value not in AttendanceCorrection.Value.values:
        raise AttendanceError('Неизвестное значение.')
    if not reason:
        raise AttendanceError('Укажите причину исправления.')
    with transaction.atomic():
        participant = MeetingParticipant.objects.select_for_update().select_related('meeting__project').get(
            pk=participant.pk)
        meeting = participant.meeting
        if not services.can_correct(user, meeting):
            raise PermissionDenied('Исправляет руководитель или тимлид этого направления.')
        if meeting.kind == VideoMeeting.Kind.PM_LEADS:
            raise AttendanceError('Посещаемость встреч ПМ с тимлидами не учитывается.')
        old = compute(meeting, [participant])[0].value
        if old == f'manual_{value}':
            raise AttendanceError('Значение уже такое.')
        item = AttendanceCorrection.objects.create(participant=participant, old_value=old, new_value=value,
                                                   reason=reason[:2000], changed_by=user)
        audit_log(meeting, 'Посещаемость исправлена', user=user, old_value=f'{participant.person_name}: {LABELS[old]}',
                  new_value=AttendanceCorrection.Value(value).label, reason=reason)
    return item


def detail_context(request, scope, meeting, participants) -> dict:
    from django.urls import reverse

    rows = compute(meeting, participants)
    prefix = {'head': 'video:correct', 'lead': 'lead_portal:meeting_correct', 'pm': ''}[scope]
    for row in rows:
        row.ui_modal, row.ui_form = f'fix-{row.participant.pk}', f'fix-form-{row.participant.pk}'
        row.ui_url = reverse(prefix, args=[meeting.pk, row.participant.pk]) if prefix else ''
    events = list(meeting.events.select_related('participant').order_by('occurred_at', 'pk')[:300])
    issues = identity_issues(meeting)
    can_fix = services.can_correct(request.user, meeting) and meeting.kind != VideoMeeting.Kind.PM_LEADS
    return {
        'attendance_block': True, 'presence': rows, 'events': events, 'issues': issues,
        'can_fix': can_fix and bool(prefix), 'fix_values': AttendanceCorrection.Value.choices,
        'covered': coverage(meeting), 'track_attendance': meeting.kind != VideoMeeting.Kind.PM_LEADS,
        'present_count': sum(1 for r in rows if r.present), 'parallel_count': sum(1 for r in rows if r.parallel),
    }

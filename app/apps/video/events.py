"""Приём событий видеосервиса (этап 9B).

POST /video/webhook/<provider>/ — только провайдер из VIDEO_PROVIDER, только с
верной подписью (X-Jaas-Signature v1, окно 5 минут). Повтор с тем же
idempotencyKey ничего не меняет. Порядок событий не гарантирован (JaaS) —
поэтому ничего не выводим из порядка прихода, только из времени события.

Личность: событие «подтверждено», только если data.id совпал с нашим
provider_user_id участника ИМЕННО этой встречи (его знает только его
персональная ссылка). Имя в видеосервисе не используется для опознания.
Проведённым провайдер делает собрание, когда в комнате были и ведущий, и
хотя бы один подтверждённый участник.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.video.models import MeetingParticipant, ProviderEvent, VideoMeeting
from apps.video.providers import get_provider
from apps.video.providers.base import ProviderError

logger = logging.getLogger(__name__)
HANDLED = {'ROOM_CREATED', 'ROOM_DESTROYED', 'PARTICIPANT_JOINED', 'PARTICIPANT_LEFT'}
V = ProviderEvent.Verification


def ingest(provider_name: str, event) -> str:
    if event.event_type not in HANDLED:
        return 'ignored'
    key = f'{provider_name}:{event.idempotency_key}'
    if ProviderEvent.objects.filter(idempotency_key=key).exists():
        return 'duplicate'
    meeting = VideoMeeting.objects.filter(room=event.room, provider=provider_name).first()
    participant = None
    if meeting is None:
        verification = V.UNKNOWN_ROOM
    elif event.event_type.startswith('ROOM_'):
        verification = V.ROOM
    elif event.user_id and event.user_id == meeting.host_user_id:
        verification = V.HOST
    else:
        participant = MeetingParticipant.objects.filter(meeting=meeting, provider_user_id=event.user_id).first() \
            if event.user_id else None
        verification = V.VERIFIED if participant else V.UNVERIFIED
    try:
        with transaction.atomic():
            ProviderEvent.objects.create(
                provider=provider_name, idempotency_key=key, event_type=event.event_type, meeting=meeting,
                participant=participant, provider_user_id=event.user_id, display_name=event.display_name,
                provider_participant_id=event.participant_id, session_id=event.session_id,
                disconnect_reason=event.disconnect_reason, verification=verification, occurred_at=event.occurred_at)
            if meeting is not None:
                _update_meeting(meeting, event)
    except IntegrityError:
        return 'duplicate'
    return verification


def _update_meeting(meeting, event):
    from django.utils import timezone

    meeting = VideoMeeting.objects.select_for_update().get(pk=meeting.pk)
    fields = []
    if event.event_type == 'ROOM_CREATED' and (meeting.room_started_at is None
                                                or event.occurred_at < meeting.room_started_at):
        meeting.room_started_at = event.occurred_at
        fields.append('room_started_at')
    if event.event_type == 'ROOM_DESTROYED' and (meeting.room_ended_at is None
                                                  or event.occurred_at > meeting.room_ended_at):
        meeting.room_ended_at = event.occurred_at
        fields.append('room_ended_at')
    if meeting.status == VideoMeeting.Status.PLANNED and event.event_type == 'PARTICIPANT_JOINED':
        joins = meeting.events.filter(event_type='PARTICIPANT_JOINED')
        host = joins.filter(verification=V.HOST).order_by('occurred_at').first()
        guest = joins.filter(verification=V.VERIFIED).order_by('occurred_at').first()
        if host and guest:
            meeting.status, meeting.held_source = VideoMeeting.Status.HELD, VideoMeeting.HeldSource.PROVIDER
            meeting.held_at = max(host.occurred_at, guest.occurred_at)
            fields += ['status', 'held_source', 'held_at']
    if fields:
        meeting.updated_at = timezone.now()
        meeting.save(update_fields=fields + ['updated_at'])


@csrf_exempt
@require_POST
def webhook(request, provider):
    if provider != getattr(settings, 'VIDEO_PROVIDER', 'simulator'):
        return HttpResponse(status=404)
    adapter = get_provider(provider)
    body = request.body
    if len(body) > 256 * 1024:
        return HttpResponse(status=413)
    if not adapter.verify_webhook(request.headers.get(adapter.SIGNATURE_HEADER, ''), body):
        logger.warning('Вебхук видеосервиса: неверная подпись (provider=%s)', provider)
        return HttpResponse(status=401)
    try:
        event = adapter.parse_event(body)
    except ProviderError:
        return HttpResponse(status=400)
    return JsonResponse({'result': ingest(provider, event)})

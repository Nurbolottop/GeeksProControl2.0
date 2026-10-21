"""Симулятор видеосервиса для разработки и тестов: ссылки на несуществующий
домен (.invalid), события подписываются тем же форматом, что JaaS, секретом
VIDEO_SIMULATOR_SECRET. Не является реальной интеграцией: на его данных
посещаемость помечается как полученная от симулятора."""
from __future__ import annotations

import json
import secrets
import time

from django.conf import settings

from apps.video.providers.base import VideoProvider


class SimulatorProvider(VideoProvider):
    name = 'simulator'
    supports_identity = True
    supports_events = True

    def configured(self) -> bool:
        return bool(self.webhook_secret())

    def missing_settings(self) -> list[str]:
        return [] if self.configured() else ['VIDEO_SIMULATOR_SECRET']

    def webhook_secret(self) -> str:
        return (getattr(settings, 'VIDEO_SIMULATOR_SECRET', '') or '').strip()

    def join_url(self, meeting, *, user_id, name, moderator=False) -> str:
        return f'https://video-simulator.invalid/{meeting.room}?u={user_id}'

    def event(self, event_type, meeting, *, user_id='', name='', participant_id='', key=None, ts=None,
              reason='', signed_at=None) -> tuple[bytes, str]:
        """(тело, заголовок подписи) — как прислал бы JaaS: время события (ts) в
        теле, время отправки (signed_at) — в подписи."""
        ts = int(ts or time.time())
        signed_at = int(signed_at or time.time())
        body = json.dumps({
            'idempotencyKey': key or secrets.token_hex(8), 'eventType': event_type, 'sessionId': 's1',
            'timestamp': ts * 1000, 'fqn': f'sim/{meeting.room}', 'appId': 'sim',
            'data': {'id': user_id, 'name': name, 'participantId': participant_id or user_id,
                     'disconnectReason': reason},
        }).encode()
        return body, self.sign(self.webhook_secret(), body, signed_at)

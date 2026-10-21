"""Единый интерфейс видеопровайдера.

Провайдер умеет:
* prepare_room(meeting) — имя комнаты (в JaaS комната создаётся при входе,
  внешнего API не нужно — сеть не трогаем);
* join_url(meeting, user_id, name, moderator) — персональная ссылка с
  подписанным JWT (userId = наш случайный provider_user_id);
* verify_webhook(headers, body) — подлинность входящего события;
* parse_event(body) — нормализованное событие.
Секреты — только из окружения (настройки), не из кода.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import time
from dataclasses import dataclass


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class Event:
    idempotency_key: str
    event_type: str
    room: str
    occurred_at: datetime.datetime
    user_id: str = ''
    participant_id: str = ''
    display_name: str = ''
    session_id: str = ''
    disconnect_reason: str = ''


class VideoProvider:
    name = 'base'
    supports_identity = False      # провайдер возвращает наш userId в событиях
    supports_events = False        # провайдер присылает события подключения
    SIGNATURE_HEADER = 'X-Jaas-Signature'
    TOLERANCE_SECONDS = 300

    def configured(self) -> bool:
        return False

    def missing_settings(self) -> list[str]:
        return []

    def prepare_room(self, meeting) -> str:
        return meeting.room

    def join_url(self, meeting, *, user_id, name, moderator=False) -> str:  # pragma: no cover
        raise NotImplementedError

    def webhook_secret(self) -> str:
        return ''

    # --- подпись в формате JaaS: t=<unix>,v1=<base64 HMAC-SHA256("t." + body)>
    @staticmethod
    def sign(secret: str, body: bytes, timestamp: int) -> str:
        payload = f'{timestamp}.'.encode() + body
        digest = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
        return f't={timestamp},v1={base64.b64encode(digest).decode()}'

    def verify_webhook(self, header: str, body: bytes, now: float | None = None) -> bool:
        secret = self.webhook_secret()
        if not secret or not header:
            return False
        parts = {}
        signatures = []
        for item in header.split(','):
            key, _, value = item.strip().partition('=')
            if key == 'v1':
                signatures.append(value)  # прочие схемы игнорируем (защита от понижения)
            elif key == 't':
                parts['t'] = value
        if not parts.get('t', '').isdigit() or not signatures:
            return False
        timestamp = int(parts['t'])
        if abs((now or time.time()) - timestamp) > self.TOLERANCE_SECONDS:
            return False  # старое событие — защита от повторной подстановки
        expected = self.sign(secret, body, timestamp).split('v1=', 1)[1]
        return any(hmac.compare_digest(expected, sig) for sig in signatures)

    def parse_event(self, body: bytes) -> Event:
        try:
            data = json.loads(body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError) as error:
            raise ProviderError('тело события — не JSON') from error
        if not isinstance(data, dict):
            raise ProviderError('неверная структура события')
        info = data.get('data') or {}
        fqn = str(data.get('fqn') or '')
        room = fqn.rsplit('/', 1)[-1] if fqn else str(info.get('room') or '')
        ts = data.get('timestamp')
        try:
            occurred = datetime.datetime.fromtimestamp(int(ts) / (1000 if int(ts) > 10 ** 11 else 1),
                                                       tz=datetime.timezone.utc)
        except (TypeError, ValueError):
            raise ProviderError('нет времени события') from None
        key = str(data.get('idempotencyKey') or '')
        if not key or not data.get('eventType'):
            raise ProviderError('нет idempotencyKey или eventType')
        return Event(idempotency_key=key[:128], event_type=str(data['eventType'])[:40], room=room[:64],
                     occurred_at=occurred, user_id=str(info.get('id') or '')[:64],
                     participant_id=str(info.get('participantId') or '')[:128],
                     display_name=str(info.get('name') or '')[:255],
                     session_id=str(data.get('sessionId') or '')[:128],
                     disconnect_reason=str(info.get('disconnectReason') or '')[:40])

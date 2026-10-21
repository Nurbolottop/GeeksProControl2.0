"""Jitsi as a Service (8x8) — по официальной документации developer.8x8.com/jaas:
* JWT RS256: header kid = «<AppID>/<key id>»; claims aud='jitsi', iss='chat',
  sub=<AppID>, room, nbf, exp, context.user {id, name, moderator: "true"/"false"};
* в вебхуках PARTICIPANT_JOINED/LEFT поле data.id — userId из JWT;
* подпись вебхука — X-Jaas-Signature: t=…,v1=… (HMAC-SHA256, base64), только v1;
* доставка возможна повторно — idempotencyKey.
Комната создаётся при входе по ссылке — внешний API Control не вызывает.
Настройки (из окружения): JAAS_APP_ID, JAAS_KEY_ID, JAAS_PRIVATE_KEY_PATH,
JAAS_WEBHOOK_SECRET.
"""
from __future__ import annotations

import datetime
import urllib.parse

from django.conf import settings
from django.utils import timezone

from apps.video.providers.base import ProviderError, VideoProvider

JAAS_HOST = 'https://8x8.vc'


class JaasProvider(VideoProvider):
    name = 'jaas'
    supports_identity = True
    supports_events = True

    def _setting(self, key):
        return (getattr(settings, key, '') or '').strip()

    def missing_settings(self) -> list[str]:
        return [k for k in ('JAAS_APP_ID', 'JAAS_KEY_ID', 'JAAS_PRIVATE_KEY_PATH', 'JAAS_WEBHOOK_SECRET')
                if not self._setting(k)]

    def configured(self) -> bool:
        return not self.missing_settings()

    def webhook_secret(self) -> str:
        return self._setting('JAAS_WEBHOOK_SECRET')

    def _private_key(self) -> str:
        path = self._setting('JAAS_PRIVATE_KEY_PATH')
        try:
            with open(path, encoding='utf-8') as handle:
                return handle.read()
        except OSError as error:
            raise ProviderError('ключ JaaS недоступен') from error

    def token(self, meeting, *, user_id, name, moderator=False, now=None) -> str:
        from google.auth import crypt, jwt

        if not self.configured():
            raise ProviderError('JaaS не настроен: ' + ', '.join(self.missing_settings()))
        app_id = self._setting('JAAS_APP_ID')
        now = now or timezone.now()
        start = meeting.starts_at - datetime.timedelta(minutes=30)
        end = meeting.starts_at + datetime.timedelta(minutes=meeting.duration_minutes + 120)
        payload = {
            'aud': 'jitsi', 'iss': 'chat', 'sub': app_id, 'room': meeting.room,
            'nbf': int(min(start, now).timestamp()), 'exp': int(end.timestamp()),
            'context': {'user': {'id': user_id, 'name': name, 'moderator': 'true' if moderator else 'false'},
                        'features': {'recording': False, 'livestreaming': False, 'transcription': False}},
        }
        signer = crypt.RSASigner.from_string(self._private_key(), key_id=f'{app_id}/{self._setting("JAAS_KEY_ID")}')
        return jwt.encode(signer, payload).decode()

    def release_gate(self) -> str:
        """Production-вход через JaaS — только после проверки в консоли JaaS, что
        без JWT в комнату не войти (анонимный доступ — настраиваемая опция
        провайдера; в документации способ запрета не подтверждён)."""
        from django.conf import settings as conf

        if conf.GP2_ENVIRONMENT == 'production' and not getattr(conf, 'JAAS_ANONYMOUS_BLOCKED_VERIFIED', False):
            return ('release-gate: не подтверждено, что JaaS запрещает вход без JWT '
                    '(JAAS_ANONYMOUS_BLOCKED_VERIFIED). Обязательные собрания через JaaS не запускаются.')
        return ''

    def join_url(self, meeting, *, user_id, name, moderator=False) -> str:
        gate = self.release_gate()
        if gate:
            raise ProviderError(gate)
        app_id = self._setting('JAAS_APP_ID')
        token = self.token(meeting, user_id=user_id, name=name, moderator=moderator)
        return f'{JAAS_HOST}/{urllib.parse.quote(app_id)}/{urllib.parse.quote(meeting.room)}?jwt={token}'

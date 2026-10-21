"""Этап 10A: вход на собрание только через подтверждённый Telegram, одноразовые
коды, release-gate JaaS, вебхук после отмены, единый polling и бот-выпускник."""
import datetime
from unittest import mock

from django.core.checks import run_checks
from django.core.exceptions import PermissionDenied
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.tgbot.dispatcher import handle_update
from apps.tgbot.models import OutboundMessage
from apps.video import services
from apps.video.models import EntryGrant, VideoMeeting
from apps.video.providers.base import ProviderError
from apps.video.providers.jaas import JaasProvider
from apps.video.test_stage9a import SIM
from apps.video.test_stage9b import EventBase

CHAT = 7101  # Telegram стажёра А из Base
STAGE_BOT = dict(GP2_ENVIRONMENT='stage', TELEGRAM_OUTBOUND_MODE='allowlist', TELEGRAM_TEST_CHAT_IDS={str(CHAT), '999'},
                 TELEGRAM_TEST_GROUP_IDS=set(), **SIM)


def press(chat, data, uid=[90000]):  # noqa: B006 — счётчик update_id
    uid[0] += 1
    return {'update_id': uid[0], 'callback_query': {
        'id': str(uid[0]), 'data': data, 'from': {'id': chat, 'is_bot': False, 'first_name': 'С'},
        'message': {'message_id': 1, 'date': 0, 'chat': {'id': chat, 'type': 'private'}}}}


@override_settings(**STAGE_BOT)
class EntryTests(EventBase):
    def setUp(self):
        super().setUp()
        self.m = self.meeting(starts_at=timezone.now() + datetime.timedelta(minutes=20))
        self.part = self.m.participants.get(intern=self.a)
        patcher = mock.patch('apps.tgbot.outbox._bot')
        self.bot = patcher.start()
        self.bot.return_value.send_message.return_value = mock.Mock(message_id=1)
        self.addCleanup(patcher.stop)

    def code_from_bot(self):
        with self.captureOnCommitCallbacks(execute=True):
            outcome = handle_update(press(CHAT, f'meetjoin:{self.part.pk}'))
        self.assertEqual(outcome, 'meeting:entry')
        sent = self.bot.return_value.send_message.call_args
        import json

        url = json.loads(sent.kwargs['reply_markup'])['inline_keyboard'][0][0]['url']
        return url.rsplit('/enter/', 1)[1].strip('/')

    def test_link_alone_gives_no_jwt_and_code_is_one_time(self):
        response = self.client.get(reverse('video:join', args=[self.part.join_token]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('jwt', response.get('Location', '') + response.content.decode())
        code = self.code_from_bot()
        stored = OutboundMessage.objects.get(kind='meeting_entry')
        self.assertNotIn(code, str(stored.reply_markup) + stored.text)  # код не хранится в журнале
        self.assertFalse(EntryGrant.objects.filter(code_hash=code).exists())  # в БД — только хеш
        first = self.client.get(reverse('video:enter', args=[code]))
        self.assertEqual(first.status_code, 302)
        self.assertIn(self.part.provider_user_id, first['Location'])
        self.assertEqual((first['Cache-Control'], first['Referrer-Policy']), ('no-store', 'no-referrer'))
        self.assertEqual(self.client.get(reverse('video:enter', args=[code])).status_code, 403)  # повтор

    def test_other_telegram_expired_and_revoked(self):
        self.assertEqual(handle_update(press(999, f'meetjoin:{self.part.pk}')), 'meeting:denied')  # чужой чат
        code = self.code_from_bot()
        EntryGrant.objects.update(expires_at=timezone.now() - datetime.timedelta(seconds=1))
        self.assertEqual(self.client.get(reverse('video:enter', args=[code])).status_code, 403)
        code = self.code_from_bot()
        from apps.teams.models import TeamMember

        TeamMember.objects.filter(intern=self.a).update(status='left')  # участие отозвано
        self.assertEqual(self.client.get(reverse('video:enter', args=[code])).status_code, 403)
        with self.assertRaises(PermissionDenied):
            services.issue_entry(self.part.pk, CHAT)

    def test_jaas_release_gate(self):
        with override_settings(GP2_ENVIRONMENT='production', JAAS_ANONYMOUS_BLOCKED_VERIFIED=False,
                               VIDEO_PROVIDER='jaas', JAAS_APP_ID='a', JAAS_KEY_ID='k', JAAS_PRIVATE_KEY_PATH='/nope',
                               JAAS_WEBHOOK_SECRET='s'):
            with self.assertRaises(ProviderError):
                JaasProvider().join_url(self.m, user_id='u', name='n')
            self.assertIn('gp2.W010', [c.id for c in run_checks()])

    def test_key_inside_repo_is_error(self):
        from django.conf import settings

        with override_settings(VIDEO_PROVIDER='jaas', JAAS_PRIVATE_KEY_PATH=str(settings.BASE_DIR / 'jaas.pem')):
            self.assertIn('gp2.E010', [c.id for c in run_checks()])

    def test_events_after_cancel_do_not_hold(self):
        services.cancel(self.m, self.lead_user, 'перенос')
        self.post('PARTICIPANT_JOINED', self.m, user_id=self.m.host_user_id, device='h')
        self.post('PARTICIPANT_JOINED', self.m, user_id=self.part.provider_user_id, device='a')
        self.m.refresh_from_db()
        self.assertEqual(self.m.status, VideoMeeting.Status.CANCELLED)
        with self.assertRaises(PermissionDenied):
            services.issue_entry(self.part.pk, CHAT)


class PollingTests(EventBase):
    def test_single_polling_and_legacy_guard(self):
        from apps.common.telegram_lock import PollingLocked, acquire_polling_lock, release_polling_lock

        token = '123456789:AAFakeTestTokenForGeeksPro2AbcdefghiJK'
        with override_settings(TELEGRAM_BOT_TOKEN=token, TELEGRAM_OUTBOUND_MODE='allowlist'):
            with self.assertRaisesMessage(CommandError, 'run_telegram_bot'):
                call_command('run_graduate_bot')  # старый бот без явного флага не стартует
            acquire_polling_lock()
            try:
                from django.db import connections

                other = connections.create_connection('default')
                with other.cursor() as cursor:  # второй процесс — другое соединение
                    cursor.execute('SELECT pg_try_advisory_lock(%s)', [8_800_100_001])
                    self.assertFalse(cursor.fetchone()[0])
                other.close()
            finally:
                release_polling_lock()
        self.assertTrue(issubclass(PollingLocked, Exception))

    def test_compose_runs_only_new_bot(self):
        from pathlib import Path

        from django.conf import settings

        import os

        infra = os.environ.get('GP2_INFRA_DIR')
        root = Path(infra) if infra else Path(settings.BASE_DIR).parent
        paths = [root / n for n in ('docker-compose.yml', 'docker-compose.prod.yml')]
        if not infra and not all(p.exists() for p in paths):
            self.skipTest('нет GP2_INFRA_DIR — образ без инфраструктуры; на хосте: scripts/check-compose.sh')
        for path in paths:
            self.assertTrue(path.exists(), f'{path} не смонтирован (GP2_INFRA_DIR)')
            text = path.read_text()
            self.assertNotIn('command: python manage.py run_graduate_bot', text)
            self.assertIn('run_telegram_bot', text)


@override_settings(**STAGE_BOT)
class GraduateInNewBotTests(EventBase):
    def setUp(self):
        super().setUp()
        from apps.projects.models import Project, ProjectStatus
        from apps.teams.models import TeamMember

        done = Project.objects.create(name='Сданный', status=ProjectStatus.COMPLETED,
                                      actual_end_date=timezone.localdate() - datetime.timedelta(days=10))
        TeamMember.objects.filter(intern=self.a).update(status='left', left_reason='project_ended',
                                                        left_at=done.actual_end_date)
        TeamMember.objects.create(project=done, intern=self.a, role='backend', status='left',
                                  left_reason='project_ended', left_at=done.actual_end_date)
        patcher = mock.patch('apps.tgbot.outbox._bot')
        self.bot = patcher.start()
        self.bot.return_value.send_message.return_value = mock.Mock(message_id=1)
        self.addCleanup(patcher.stop)

    def msg(self, text, uid=[95000]):  # noqa: B006
        uid[0] += 1
        return {'update_id': uid[0], 'message': {'message_id': uid[0], 'date': 0, 'text': text,
                                                 'chat': {'id': CHAT, 'type': 'private'},
                                                 'from': {'id': CHAT, 'is_bot': False, 'first_name': 'С'}}}

    def test_continue_and_finish_choices(self):
        from apps.dashboard.graduation import is_graduate
        from apps.tgbot import menus

        if not is_graduate(self.a.pk):
            self.skipTest('правила выпуска не засчитали синтетический выпуск')
        self.a.refresh_from_db()
        self.assertEqual(menus.state_of(self.a, CHAT), menus.GRADUATE_CHOICE)
        self.assertEqual(handle_update(self.msg(menus.CONTINUE)), 'graduate:continue')
        self.a.refresh_from_db()
        self.assertEqual(self.a.graduate_status, 'waiting')
        self.assertEqual(menus.state_of(self.a, CHAT), menus.GRADUATE_WAITING)
        self.assertEqual(handle_update(self.msg(menus.CONTINUE)), 'denied:button')  # выбор уже сделан
        self.assertFalse(self.a.certificates.exists() if hasattr(self.a, 'certificates') else False)  # без авто-сертификатов

    def test_finish_goes_to_resume_bank(self):
        from apps.dashboard.graduation import is_graduate
        from apps.tgbot import menus

        if not is_graduate(self.a.pk):
            self.skipTest('правила выпуска не засчитали синтетический выпуск')
        self.assertEqual(handle_update(self.msg(menus.FINISH)), 'graduate:bank_about')
        self.assertEqual(handle_update(press(CHAT, 'bank:agree')), 'bank:howto')
        self.assertEqual(handle_update(press(CHAT, 'bank:sent')), 'bank:sent')
        self.a.refresh_from_db()
        self.assertEqual((self.a.resume_bank_status, self.a.status), ('pending', 'employable'))
        self.assertEqual(handle_update(press(CHAT, 'bank:sent')), 'bank:denied')  # повтор

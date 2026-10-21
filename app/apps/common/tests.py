import tempfile
from pathlib import Path
from unittest import mock

import telebot
from django.contrib.auth import get_user_model
from django.core.checks import run_checks
from django.test import TestCase, override_settings

from apps.common import outbound
from apps.common.safety import is_protected, sha256

GP1_TOKEN = '111111:gp1-production-token'
GP1_HASHES = {sha256(GP1_TOKEN)}


class ProtectedValuesTests(TestCase):
    @override_settings(PROTECTED_VALUE_HASHES=GP1_HASHES)
    def test_gp1_value_is_recognised_by_hash(self):
        self.assertTrue(is_protected(GP1_TOKEN))
        self.assertTrue(is_protected(f'  {GP1_TOKEN}\n'))
        self.assertFalse(is_protected('222222:gp2-test-token'))
        self.assertFalse(is_protected(''))

    @override_settings(PROTECTED_VALUE_HASHES=GP1_HASHES, TELEGRAM_BOT_TOKEN=GP1_TOKEN)
    def test_system_check_fails_on_gp1_token(self):
        ids = [error.id for error in run_checks()]
        self.assertIn('gp2.E001', ids)

    @override_settings(PROTECTED_VALUE_HASHES=GP1_HASHES, TELEGRAM_BOT_TOKEN='222222:gp2-test-token')
    def test_system_check_passes_on_own_token(self):
        ids = [error.id for error in run_checks()]
        self.assertNotIn('gp2.E001', ids)

    @override_settings(TELEGRAM_OUTBOUND_MODE='everyone')
    def test_unknown_outbound_mode_is_an_error(self):
        ids = [error.id for error in run_checks()]
        self.assertIn('gp2.E002', ids)

    @override_settings(TELEGRAM_OUTBOUND_MODE='on', GP2_ENVIRONMENT='stage')
    def test_sending_to_everyone_is_forbidden_on_stage(self):
        ids = [error.id for error in run_checks()]
        self.assertIn('gp2.E003', ids)

    @override_settings(TELEGRAM_OUTBOUND_MODE='allowlist', GP2_ENVIRONMENT='stage')
    def test_allowlist_is_fine_on_stage(self):
        ids = [error.id for error in run_checks()]
        self.assertNotIn('gp2.E003', ids)

    @override_settings(TELEGRAM_OUTBOUND_MODE='on', GP2_ENVIRONMENT='production')
    def test_sending_to_everyone_is_allowed_only_in_production(self):
        ids = [error.id for error in run_checks()]
        self.assertNotIn('gp2.E003', ids)

    @override_settings(
        PROTECTED_VALUE_HASHES={sha256('gp1-sheet')}, RESERVE_SHEET_ID='gp1-sheet',
        GOOGLE_SHEETS_CREDENTIALS_FILE=__file__,
    )
    def test_gp1_sheet_disables_export(self):
        from apps.reserve import gsheets

        self.assertFalse(gsheets.is_configured())
        with self.assertRaises(RuntimeError):
            gsheets.push(groups={})


class OutboundTests(TestCase):
    def setUp(self):
        self.bot = outbound.GuardedTeleBot('222222:gp2-test-token')
        outbound.OUTBOX.clear()

    @override_settings(TELEGRAM_OUTBOUND_MODE='off')
    def test_off_mode_never_reaches_telegram(self):
        with mock.patch.object(telebot.TeleBot, 'send_message') as real_send:
            self.assertIsNone(self.bot.send_message(555, 'привет'))
        real_send.assert_not_called()
        self.assertEqual(list(outbound.OUTBOX), [(555, 'привет')])

    @override_settings(TELEGRAM_OUTBOUND_MODE='allowlist', TELEGRAM_TEST_CHAT_IDS={'777'})
    def test_allowlist_mode_writes_only_to_test_chats(self):
        with mock.patch.object(telebot.TeleBot, 'send_message') as real_send:
            self.bot.send_message(777, 'тестировщику')
            self.bot.send_message(555, 'стажёру')
        real_send.assert_called_once()
        self.assertEqual(real_send.call_args.args[0], 777)
        self.assertEqual(list(outbound.OUTBOX), [(555, 'стажёру')])

    @override_settings(TELEGRAM_OUTBOUND_MODE='on', GP2_ENVIRONMENT='production')
    def test_on_mode_sends_in_production(self):
        with mock.patch.object(telebot.TeleBot, 'send_message') as real_send:
            self.bot.send_message(555, 'всем')
        real_send.assert_called_once()

    @override_settings(TELEGRAM_OUTBOUND_MODE='on', GP2_ENVIRONMENT='stage')
    def test_on_mode_is_muted_outside_production(self):
        with mock.patch.object(telebot.TeleBot, 'send_message') as real_send:
            self.bot.send_message(555, 'всем')
        real_send.assert_not_called()
        self.assertEqual(list(outbound.OUTBOX), [(555, 'всем')])

    def test_graduate_bot_is_guarded(self):
        from apps.graduate_bot.bot import bot

        self.assertIsInstance(bot, outbound.GuardedTeleBot)


class ProtectedMediaTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        root = Path(self.media.name)
        (root / 'documents').mkdir()
        (root / 'documents' / 'contract.txt').write_text('договор')
        (root / 'reserve' / 'photo').mkdir(parents=True)
        (root / 'reserve' / 'photo' / 'face.jpg').write_bytes(b'jpg')
        self.override = override_settings(MEDIA_ROOT=self.media.name, MEDIA_ACCEL_REDIRECT_PREFIX='')
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = get_user_model().objects.create_user(username='media-user', password='x-Strong-pass-1')

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get('/media/documents/contract.txt')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_public_photo_opens_without_login(self):
        response = self.client.get('/media/reserve/photo/face.jpg')
        self.assertEqual(response.status_code, 200)

    def test_signed_in_user_gets_file(self):
        self.client.force_login(self.user)
        response = self.client.get('/media/documents/contract.txt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content).decode(), 'договор')

    def test_path_traversal_is_rejected(self):
        self.client.force_login(self.user)
        response = self.client.get('/media/../core/settings/base.py')
        self.assertIn(response.status_code, (400, 404))
        response = self.client.get('/media/documents/../../../etc/passwd')
        self.assertEqual(response.status_code, 404)

    def test_production_hands_file_to_nginx(self):
        self.client.force_login(self.user)
        with override_settings(MEDIA_ACCEL_REDIRECT_PREFIX='/protected-media/'):
            response = self.client.get('/media/documents/contract.txt')
        self.assertEqual(response['X-Accel-Redirect'], '/protected-media/documents/contract.txt')
        self.assertEqual(response.content, b'')

    def test_healthz(self):
        response = self.client.get('/healthz/')
        self.assertEqual(response.status_code, 200)

"""Telegram: защита запросов, конфигурация, секреты. Без реальной сети —
HTTP-сессия pyTelegramBotAPI заменена моком, и тесты проверяют, что
заблокированный запрос до неё вообще не доходит."""
import importlib.util
import os
import stat
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

import telebot
from django.conf import settings
from django.core.checks import run_checks
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings

from apps.common import outbound, telegram_guard
from apps.common.safety import sha256

TOKEN = '123456789:AAFakeTestTokenForGeeksPro2AbcdefghiJK'
SECRET = TOKEN.split(':', 1)[1]
USER, STRANGER, GROUP, OTHER_GROUP = '111222333', '999888777', '-1001234567890', '-1009999999999'


def fake_response(result=None):
    response = mock.Mock(status_code=200)
    response.json.return_value = {'ok': True, 'result': result if result is not None else True}
    return response


def session_mock():
    session = mock.Mock()
    session.request.return_value = fake_response({'message_id': 1, 'date': 0, 'chat': {'id': 1, 'type': 'private'},
                                                  'message_thread_id': 7, 'name': 'Backend', 'icon_color': 1,
                                                  'id': 1, 'is_bot': True, 'first_name': 'bot', 'username': 'GeeksProAutoBot'})
    return mock.patch('telebot.apihelper._get_req_session', return_value=session), session


ALLOWLIST = dict(TELEGRAM_OUTBOUND_MODE='allowlist', GP2_ENVIRONMENT='stage',
                 TELEGRAM_TEST_CHAT_IDS={USER}, TELEGRAM_TEST_GROUP_IDS={GROUP})


class GuardInstallTests(SimpleTestCase):
    def test_guard_is_installed_at_startup(self):
        self.assertTrue(telegram_guard.is_installed())


class OffModeTests(SimpleTestCase):
    @override_settings(TELEGRAM_OUTBOUND_MODE='off', GP2_ENVIRONMENT='stage')
    def test_off_blocks_every_api_call_before_network(self):
        bot = telebot.TeleBot(TOKEN)
        patcher, session = session_mock()
        with patcher:
            for call in (bot.get_me, lambda: bot.get_updates(), lambda: bot.send_message(USER, 'x'),
                         lambda: bot.create_forum_topic(GROUP, 'Backend'), lambda: bot.set_webhook('https://example.com/hook')):
                with self.subTest(call=call), self.assertRaises(telegram_guard.TelegramBlocked):
                    call()
        session.request.assert_not_called()

    @override_settings(TELEGRAM_OUTBOUND_MODE='off', GP2_ENVIRONMENT='stage')
    def test_guarded_bot_stub_does_not_touch_network(self):
        bot = outbound.GuardedTeleBot(TOKEN)
        patcher, session = session_mock()
        with patcher:
            self.assertIsNone(bot.send_message(USER, 'привет'))
        session.request.assert_not_called()

    @override_settings(TELEGRAM_OUTBOUND_MODE='on', GP2_ENVIRONMENT='stage')
    def test_mass_mode_is_blocked_on_stage(self):
        bot = telebot.TeleBot(TOKEN)
        patcher, session = session_mock()
        with patcher, self.assertRaises(telegram_guard.TelegramBlocked):
            bot.send_message(USER, 'всем')
        session.request.assert_not_called()


@override_settings(**ALLOWLIST)
class AllowlistTests(SimpleTestCase):
    def setUp(self):
        self.bot = telebot.TeleBot(TOKEN)

    def test_allowlisted_user_and_group_pass(self):
        patcher, session = session_mock()
        with patcher:
            self.bot.send_message(USER, 'тест')
            self.bot.send_message(GROUP, 'тест в группу')
        self.assertEqual(session.request.call_count, 2)

    def test_unknown_telegram_id_is_blocked(self):
        patcher, session = session_mock()
        with patcher, self.assertRaises(telegram_guard.TelegramBlocked):
            self.bot.send_message(STRANGER, 'стажёру')
        session.request.assert_not_called()

    def test_topics_only_in_allowlisted_groups(self):
        patcher, session = session_mock()
        with patcher:
            with self.assertRaises(telegram_guard.TelegramBlocked):
                self.bot.create_forum_topic(OTHER_GROUP, 'Backend')
            with self.assertRaises(telegram_guard.TelegramBlocked):
                self.bot.create_forum_topic(USER, 'Backend')  # личный чат — не группа
            self.assertEqual(session.request.call_count, 0)
            self.bot.create_forum_topic(GROUP, 'Backend')
        self.assertEqual(session.request.call_count, 1)

    def test_webhook_and_targetless_calls_blocked_polling_allowed(self):
        patcher, session = session_mock()
        with patcher:
            with self.assertRaises(telegram_guard.TelegramBlocked):
                self.bot.set_webhook('https://example.com/hook')
            with self.assertRaises(telegram_guard.TelegramBlocked):
                self.bot.answer_callback_query('cb-1')
            self.bot.get_me()
        self.assertEqual(session.request.call_count, 1)

    def test_token_never_appears_in_errors_or_logs(self):
        patcher, _session = session_mock()
        with patcher, self.assertLogs('apps.common.telegram_guard', level='INFO') as logs, \
                self.assertRaises(telegram_guard.TelegramBlocked) as ctx:
            self.bot.send_message(STRANGER, 'x')
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertNotIn(SECRET, '\n'.join(logs.output))


class ProductionTests(SimpleTestCase):
    @override_settings(TELEGRAM_OUTBOUND_MODE='on', GP2_ENVIRONMENT='production',
                       TELEGRAM_TEST_CHAT_IDS=set(), TELEGRAM_TEST_GROUP_IDS=set())
    def test_production_sends_but_topics_need_known_group(self):
        bot = telebot.TeleBot(TOKEN)
        patcher, session = session_mock()
        with patcher:
            bot.send_message(STRANGER, 'prod')
            with self.assertRaises(telegram_guard.TelegramBlocked):
                bot.create_forum_topic(OTHER_GROUP, 'Backend')
        self.assertEqual(session.request.call_count, 1)


class BotStartupTests(TestCase):
    @override_settings(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_OUTBOUND_MODE='off')
    def test_bot_does_not_start_in_off_mode(self):
        with self.assertRaisesMessage(CommandError, 'off'):
            call_command('run_graduate_bot')

    @override_settings(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_OUTBOUND_MODE='allowlist',
                       PROTECTED_VALUE_HASHES={sha256(TOKEN)})
    def test_bot_refuses_gp1_token(self):
        with self.assertRaisesMessage(CommandError, '1.0'):
            call_command('run_graduate_bot')

    @override_settings(TELEGRAM_BOT_TOKEN='', TELEGRAM_OUTBOUND_MODE='allowlist')
    def test_bot_needs_token(self):
        with self.assertRaises(CommandError):
            call_command('run_graduate_bot')


class ConfigCheckTests(TestCase):
    @override_settings(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_BOT_USERNAME='GeeksProAutoBot',
                       TELEGRAM_OUTBOUND_MODE='off', GP2_ENVIRONMENT='stage')
    def test_telegram_check_is_offline_and_hides_token(self):
        patcher, session = session_mock()
        out = StringIO()
        with patcher:
            call_command('telegram_check', stdout=out)
        session.request.assert_not_called()
        text = out.getvalue()
        self.assertNotIn(SECRET, text)
        self.assertIn('@GeeksProAutoBot', text)
        self.assertIn('верный', text)
        self.assertIn('включена', text)

    def ids(self, **overrides):
        with override_settings(**overrides):
            return {e.id for e in run_checks()}

    def test_system_checks(self):
        self.assertIn('gp2.E005', self.ids(TELEGRAM_BOT_TOKEN='not-a-token'))
        self.assertNotIn('gp2.E005', self.ids(TELEGRAM_BOT_TOKEN=TOKEN))
        self.assertIn('gp2.E006', self.ids(TELEGRAM_BOT_USERNAME='GeeksPro'))
        self.assertNotIn('gp2.E006', self.ids(TELEGRAM_BOT_USERNAME='GeeksProAutoBot'))
        self.assertIn('gp2.E007', self.ids(TELEGRAM_TEST_CHAT_IDS={'-100'}))
        self.assertIn('gp2.E008', self.ids(TELEGRAM_TEST_GROUP_IDS={'12345'}))
        self.assertIn('gp2.W001', self.ids(TELEGRAM_OUTBOUND_MODE='allowlist', TELEGRAM_TEST_CHAT_IDS=set(),
                                           TELEGRAM_TEST_GROUP_IDS=set()))

    def test_settings_are_read_from_environment(self):
        self.assertEqual(settings.TELEGRAM_OUTBOUND_MODE in ('off', 'allowlist', 'on'), True)
        self.assertIsInstance(settings.TELEGRAM_TEST_GROUP_IDS, set)
        self.assertFalse(settings.TELEGRAM_BOT_USERNAME.startswith('@'))


class TokenToolTests(SimpleTestCase):
    """app/tools/set_telegram_token.py — без Django, без сети."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = Path(settings.BASE_DIR) / 'tools' / 'set_telegram_token.py'
        spec = importlib.util.spec_from_file_location('set_telegram_token', path)
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def test_format_and_gp1_token(self):
        self.assertTrue(self.tool.token_format_ok(TOKEN))
        for bad in ('', '123', 'abc:def', TOKEN + ' extra'):
            self.assertFalse(self.tool.token_format_ok(bad))
        lines = [f'PROTECTED_VALUE_HASHES=aaa,{sha256(TOKEN)}']
        self.assertTrue(self.tool.is_protected(TOKEN, lines))
        self.assertFalse(self.tool.is_protected('987654321:' + 'B' * 35, lines))

    def test_env_update_keeps_other_lines_and_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / '.env'
            env.write_text('SECRET_KEY=keep\nTELEGRAM_BOT_TOKEN=\n# comment\n')
            os.chmod(env, 0o644)
            lines = self.tool.with_value(self.tool.read_env(env), 'TELEGRAM_BOT_TOKEN', TOKEN)
            self.tool.write_env(env, lines)
            text = env.read_text()
            self.assertIn('SECRET_KEY=keep', text)
            self.assertIn('# comment', text)
            self.assertEqual(text.count('TELEGRAM_BOT_TOKEN='), 1)
            self.assertEqual(stat.S_IMODE(env.stat().st_mode), 0o600)

    def test_refuses_non_interactive_input(self):
        with mock.patch.object(self.tool, 'ENV_FILE', Path(__file__)), \
                mock.patch.object(self.tool, 'env_is_git_ignored', return_value=True), \
                mock.patch('sys.stdin', StringIO(TOKEN)), mock.patch('builtins.print'), \
                mock.patch.object(self.tool, 'write_env') as write:
            self.assertEqual(self.tool.main(), 1)
        write.assert_not_called()

    def test_tool_does_not_touch_network(self):
        source = (Path(settings.BASE_DIR) / 'tools' / 'set_telegram_token.py').read_text()
        for marker in ('api.telegram.org', 'requests', 'urllib', 'telebot', 'sys.argv'):
            self.assertNotIn(marker, source)

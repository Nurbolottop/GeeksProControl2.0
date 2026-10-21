"""Этап 8A: бот, постоянное меню, регистрация, продажи, подключение профиля,
заявки, повторы обновлений и защита. Без сети: исходящие — мок GuardedTeleBot
или мок HTTP-сессии pyTelegramBotAPI (запрос до сети не доходит)."""
import itertools
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.common import telegram_guard
from apps.interns.models import Intern, InternshipRequest, InternStatus, SalesConfirmation
from apps.projects.fixtures_closure import head
from apps.projects.models import Project
from apps.projects.services import create_project
from apps.teams.models import TeamMember, TeamRole
from apps.tgbot import menus
from apps.tgbot.dispatcher import handle_update
from apps.tgbot.models import DialogState, LinkRequest, OutboundMessage, TelegramUpdate
from apps.training.models import Specialization

USER, OTHER, STRANGER = 5001, 5002, 9009
SALES_CHAT, AGENT = -100555, 7007
STAGE = dict(GP2_ENVIRONMENT='stage', TELEGRAM_OUTBOUND_MODE='allowlist',
             TELEGRAM_TEST_CHAT_IDS={str(USER), str(OTHER)}, TELEGRAM_TEST_GROUP_IDS={str(SALES_CHAT)},
             SALES_TELEGRAM_CHAT_ID=str(SALES_CHAT), SALES_AGENT_TELEGRAM_IDS={str(AGENT)})
_ids = itertools.count(1000)


def msg(chat, text='', *, user=None, **extra):
    user = user or chat
    message = {'message_id': next(_ids), 'date': 0, 'chat': {'id': chat, 'type': 'private' if chat > 0 else 'supergroup'},
               'from': {'id': user, 'is_bot': False, 'first_name': 'Тест', 'username': f'u{user}'}, **extra}
    if text:
        message['text'] = text
    return {'update_id': next(_ids), 'message': message}


def cb(chat, data):
    return {'update_id': next(_ids), 'callback_query': {
        'id': str(next(_ids)), 'data': data, 'from': {'id': chat, 'is_bot': False, 'first_name': 'Т'},
        'message': {'message_id': next(_ids), 'date': 0, 'chat': {'id': chat, 'type': 'private'}}}}


def contact(chat, phone, owner=None):
    return msg(chat, contact={'phone_number': phone, 'user_id': owner if owner is not None else chat,
                              'first_name': 'Тест'})


class FakeBot:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return mock.Mock(message_id=len(self.sent))

    def answer_callback_query(self, *args, **kwargs):
        return True

    def last(self, chat):
        return [s for s in self.sent if s[0] == chat][-1]

    def keyboard(self, chat):
        return json.loads(self.last(chat)[2]['reply_markup'])


@override_settings(**STAGE)
class Base(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        Specialization.objects.create(name='PM')
        self.bot = FakeBot()
        patcher = mock.patch('apps.tgbot.outbox._bot', return_value=self.bot)
        patcher.start()
        self.addCleanup(patcher.stop)

    def send(self, update):
        with self.captureOnCommitCallbacks(execute=True):
            return handle_update(update)

    def labels(self, chat):
        return [b['text'] for row in self.bot.keyboard(chat)['keyboard'] for b in row]

    def register(self, chat=USER, phone='+996 700 111 222', name='Асанов Бакыт', city='Бишкек'):
        for update in (msg(chat, '/start'), msg(chat, menus.START), msg(chat, name), msg(chat, phone),
                       msg(chat, 'Backend'), msg(chat, city), cb(chat, 'reg:ok')):
            outcome = self.send(update)
        return outcome

    def sales(self, text, *, sender=AGENT, **extra):
        return self.send(msg(SALES_CHAT, text, user=sender, **extra))


def sales_text(name='Асанов Бакыт', phone='0700111222', direction='Backend', city='Бишкек', ref='ОПЛ-1'):
    return f'#оплата\nФИО: {name}\nТелефон: {phone}\nНаправление: {direction}\nГород: {city}\nОплата: {ref}'


class KeyboardAndMenuTests(Base):
    def test_persistent_keyboard_after_start(self):
        self.send(msg(USER, '/start'))
        board = self.bot.keyboard(USER)
        self.assertEqual((board['is_persistent'], board['resize_keyboard'], board['one_time_keyboard']),
                         (True, True, False))
        self.assertEqual(self.labels(USER), [menus.START, menus.ALREADY, menus.HELP])
        self.send(msg(USER, menus.HELP))  # клавиатура приходит с каждым ответом
        self.assertEqual(self.labels(USER), [menus.START, menus.ALREADY, menus.HELP])

    def test_menus_by_status_and_role(self):
        project = create_project(Project(name='Меню', recruitment_open=True))
        intern = Intern.objects.create(full_name='Активный Стажёр', specialization=self.backend,
                                       status=InternStatus.ACTIVE, telegram_chat_id=USER)
        TeamMember.objects.create(project=project, intern=intern, role=TeamRole.BACKEND, status='active')
        labels = menus.allowed_labels(intern, USER)
        for label in (menus.MY_PROJECTS, menus.MY_TASKS, menus.SUBMIT, menus.MY_STATUS, menus.FREEZE, menus.HELP):
            self.assertIn(label, labels)
        self.assertNotIn(menus.LEAVE, labels)  # один проект — выхода нет
        Intern.objects.filter(pk=intern.pk).update(status=InternStatus.PAUSED)
        intern.refresh_from_db()
        self.assertEqual(menus.buttons_for(intern, USER), [[menus.RETURN], [menus.MY_STATUS, menus.HELP]])
        user = get_user_model().objects.create_user(username='m-lead', password='x', role='team_lead')
        lead = Intern.objects.create(full_name='Тимлид Меню', user=user, specialization=self.backend, position='lead',
                                     staff_role='lead', staff_status='active', telegram_chat_id=OTHER)
        self.assertIn(menus.REVIEWS, menus.allowed_labels(lead, OTHER))
        self.assertNotIn(menus.FREEZE, menus.allowed_labels(lead, OTHER))
        Intern.objects.filter(pk=lead.pk).update(staff_status='dismissed')
        lead.refresh_from_db()
        self.assertEqual(menus.state_of(lead), menus.STOPPED)

    def test_hidden_button_is_refused_by_server(self):
        self.send(msg(USER, '/start'))
        self.assertEqual(self.send(msg(USER, menus.FREEZE)), 'denied:button')  # гость: кнопки нет
        self.assertEqual(self.send(msg(USER, '/freeze')), 'denied:button')
        self.assertFalse(InternshipRequest.objects.exists())


class RegistrationAndSalesTests(Base):
    def test_registration_survives_restart_and_waits_for_sales(self):
        self.send(msg(USER, menus.START))
        self.send(msg(USER, 'Асанов Бакыт'))
        self.assertEqual(DialogState.objects.get(chat_id=USER).state, 'reg_phone')  # шаг — в БД
        # «перезапуск»: новый процесс знает только БД
        self.send(contact(USER, '+996700111222'))
        self.send(msg(USER, 'Backend'))
        self.send(msg(USER, 'Москва'))
        self.assertEqual(DialogState.objects.get(chat_id=USER).state, 'reg_city')  # город — только Бишкек/Ош
        self.send(msg(USER, 'Бишкек'))
        self.assertEqual(self.send(cb(USER, 'reg:ok')), 'reg:done')
        intern = Intern.objects.get(telegram_chat_id=USER)
        self.assertEqual((intern.status, intern.branch, intern.specialization), (InternStatus.WAITING, 'Бишкек',
                                                                                 self.backend))
        req = InternshipRequest.objects.get(intern=intern)
        self.assertEqual((req.kind, req.sales_status), ('enroll', 'pending'))
        self.assertEqual(self.labels(USER), [menus.REQUEST_STATUS, menus.HELP])
        self.assertEqual(self.send(msg(USER, menus.AVAILABLE)), 'denied:button')  # без продаж — в проект нельзя
        self.assertEqual(self.send(cb(USER, 'reg:ok')), 'reg:stale')  # повтор подтверждения
        self.assertEqual(Intern.objects.count(), 1)

    def test_duplicate_profile_is_not_created(self):
        Intern.objects.create(full_name='Старый Профиль', phone='0700 111 222', status=InternStatus.CANCELLED)
        self.send(msg(USER, menus.START))
        self.send(msg(USER, 'Асанов Бакыт'))
        self.assertEqual(self.send(msg(USER, '+996 700 111 222')), 'reg:duplicate')
        self.assertEqual(Intern.objects.count(), 1)

    def test_sales_confirms_only_full_match(self):
        self.register()
        intern = Intern.objects.get(telegram_chat_id=USER)
        self.assertEqual(self.sales(sales_text(city='Ош', ref='A')), 'sales:mismatch')  # город не совпал
        self.assertEqual(self.sales(sales_text(direction='Frontend', ref='B')), 'sales:mismatch')
        self.assertEqual(self.sales(sales_text(name='Другой Человек', ref='C')), 'sales:mismatch')
        self.assertEqual(self.sales(sales_text(phone='0555999888', ref='D')), 'sales:unmatched')
        self.assertEqual(self.sales(sales_text(ref='E'), sender=STRANGER), 'sales:rejected')  # не сотрудник продаж
        self.assertEqual(self.sales(sales_text(ref='F'), forward_date=1), 'sales:forwarded_or_bot')
        self.assertEqual(self.sales('#оплата\nФИО: Асанов Бакыт\nТелефон: 0700111222'), 'sales:format')
        self.assertEqual(InternshipRequest.objects.get(intern=intern).sales_status, 'pending')
        update = msg(SALES_CHAT, sales_text(ref='ОПЛ-77'), user=AGENT)
        self.assertEqual(self.send(update), 'sales:applied')
        self.assertEqual(self.send(update), 'duplicate')  # повтор update
        again = dict(update, update_id=next(_ids))  # тот же message_id в новом update
        self.assertEqual(self.send(again), 'sales:applied')  # та же запись, без второго применения
        self.assertEqual(SalesConfirmation.objects.filter(status='applied').count(), 1)
        req = InternshipRequest.objects.get(intern=intern)
        self.assertEqual((req.status, req.sales_status, req.sales_reference), ('approved', 'confirmed', 'ОПЛ-77'))
        self.assertTrue(OutboundMessage.objects.filter(kind='registration_confirmed', intern=intern).exists())
        self.assertIn(menus.AVAILABLE, menus.allowed_labels(intern, USER))
        project = create_project(Project(name='Первый проект', recruitment_open=True))
        self.assertEqual(self.send(cb(USER, f'choose:{project.pk}')), 'choose:confirm')
        self.assertEqual(self.send(cb(USER, f'choose_ok:{project.pk}')), 'choose:done')
        self.assertTrue(TeamMember.objects.filter(intern=intern, project=project, status='active').exists())
        self.assertEqual(self.send(cb(USER, f'choose_ok:{project.pk}')), 'choose:denied')  # один раз

    def test_ambiguous_sales_go_to_manual(self):
        for name in ('Асанов Бакыт', 'Асанов Бакыт Маратович'):
            Intern.objects.create(full_name=name, phone='0700111222', specialization=self.backend, branch='Бишкек')
        self.assertEqual(self.sales(sales_text()), 'sales:ambiguous')
        self.assertFalse(SalesConfirmation.objects.filter(status='applied').exists())

    def test_head_cannot_approve_enrollment_instead_of_sales(self):
        from apps.interns import freeze

        self.register()
        req = InternshipRequest.objects.get(kind='enroll')
        with self.assertRaises(freeze.FreezeError):
            freeze.approve(req, self.head)


class LinkAndRequestTests(Base):
    def test_existing_intern_links_by_own_contact(self):
        intern = Intern.objects.create(full_name='Действующий Стажёр', phone='0700222333', specialization=self.backend,
                                       status=InternStatus.ACTIVE)
        self.send(msg(USER, menus.ALREADY))
        self.send(msg(USER, 'Действующий Стажёр'))
        self.assertEqual(self.send(msg(USER, '0700222333')), 'link:need_contact')  # набранный номер не принимаем
        self.assertEqual(self.send(contact(USER, '0700222333', owner=STRANGER)), 'link:refused')  # чужой контакт
        self.assertEqual(self.send(contact(USER, '+996700222333')), 'link:linked')
        intern.refresh_from_db()
        self.assertEqual((intern.telegram_chat_id, intern.status), (USER, InternStatus.ACTIVE))  # статус не тронут
        self.assertEqual(Intern.objects.count(), 1)

    def test_ambiguous_link_goes_to_head(self):
        a = Intern.objects.create(full_name='Похожий Стажёр', phone='0700444555')
        self.send(msg(USER, menus.ALREADY))
        self.send(msg(USER, 'Другой Человек'))
        self.assertEqual(self.send(contact(USER, '0700444555')), 'link:manual')
        req = LinkRequest.objects.get()
        self.assertIn(a, req.candidates.all())
        self.assertEqual(menus.state_of(None, USER), menus.PENDING)
        from apps.tgbot.people import decide_link

        with self.captureOnCommitCallbacks(execute=True):
            decide_link(req, self.head, a)
        a.refresh_from_db()
        self.assertEqual(a.telegram_chat_id, USER)
        self.client.force_login(self.head)
        self.assertEqual(self.client.get('/telegram/').status_code, 200)

    def test_freeze_return_leave_requests(self):
        from apps.interns import freeze

        projects = [create_project(Project(name=f'П{i}', recruitment_open=True)) for i in range(2)]
        intern = Intern.objects.create(full_name='Заявок Стажёр', specialization=self.backend,
                                       status=InternStatus.ACTIVE, telegram_chat_id=USER)
        for project in projects:
            TeamMember.objects.create(project=project, intern=intern, role=TeamRole.BACKEND, status='active')
        self.send(msg(USER, '/freeze'))
        self.send(msg(USER, 'Болею'))
        self.assertEqual(self.send(cb(USER, 'freeze:ok')), 'freeze:sent')
        self.assertEqual(self.send(cb(USER, 'freeze:ok')), 'freeze:stale')  # повтор нажатия
        req = InternshipRequest.objects.get(kind='freeze')
        self.assertEqual(req.source, 'bot')
        with self.captureOnCommitCallbacks(execute=True):
            freeze.approve(req, self.head)
        notice = OutboundMessage.objects.get(kind='request_decided', intern=intern)
        # меню в уведомлении обновлено под новый статус
        self.assertEqual(notice.reply_markup['keyboard'][0][0]['text'], menus.RETURN)
        self.send(msg(USER, '/return'))
        self.send(msg(USER, 'Готов'))
        self.assertEqual(self.send(cb(USER, 'return:ok')), 'return:sent')
        self.assertTrue(InternshipRequest.objects.filter(kind='return', status='pending').exists())
        self.assertEqual(self.send(msg(USER, '/leave')), 'denied:button')  # не выпускник с двумя проектами


@override_settings(**STAGE)
class InboundGateAndGuardTests(TestCase):
    def test_not_allowlisted_chat_is_skipped(self):
        with mock.patch('apps.tgbot.outbox._bot') as bot:
            self.assertEqual(handle_update(msg(STRANGER, '/start')), 'skipped:not_allowed')
            self.assertEqual(handle_update(msg(-100999, sales_text(), user=AGENT)), 'skipped:not_allowed')
            bot.assert_not_called()
        self.assertFalse(DialogState.objects.exists())
        self.assertEqual(TelegramUpdate.objects.count(), 2)

    def test_edited_message_ignored(self):
        update = {'update_id': next(_ids), 'edited_message': msg(SALES_CHAT, sales_text(), user=AGENT)['message']}
        self.assertEqual(handle_update(update), 'ignored:edited_message')

    def test_narrow_permits(self):
        telegram_guard.clear_permits()
        with self.assertRaises(telegram_guard.TelegramBlocked):
            telegram_guard.check('answerCallbackQuery', {'callback_query_id': '42'})
        with self.assertRaises(telegram_guard.TelegramBlocked):
            telegram_guard.check('getFile', {'file_id': 'F1'})
        telegram_guard.permit('callback', '42')
        telegram_guard.permit('file', 'F1')
        telegram_guard.check('answerCallbackQuery', {'callback_query_id': '42'})
        telegram_guard.check('getFile', {'file_id': 'F1'})
        with self.assertRaises(telegram_guard.TelegramBlocked):
            telegram_guard.check('getFile', {'file_id': 'F2'})
        with self.assertRaises(telegram_guard.TelegramBlocked):
            telegram_guard.check_download('documents/file_1.png')  # путь не из разрешённого getFile
        telegram_guard.permit('path', 'photos/p.png')
        telegram_guard.check_download('photos/p.png')
        with override_settings(TELEGRAM_OUTBOUND_MODE='off'):
            with self.assertRaises(telegram_guard.TelegramBlocked):
                telegram_guard.check_download('photos/p.png')
            with self.assertRaises(telegram_guard.TelegramBlocked):
                telegram_guard.check('answerCallbackQuery', {'callback_query_id': '42'})
        with override_settings(TELEGRAM_OUTBOUND_MODE='on'):
            with self.assertRaises(telegram_guard.TelegramBlocked):
                telegram_guard.check('sendMessage', {'chat_id': str(USER)})  # on вне production

    def test_outbox_holds_when_outbound_forbidden(self):
        from apps.tgbot import outbox

        with override_settings(TELEGRAM_OUTBOUND_MODE='off'):
            message = outbox.send('reply', STRANGER, 'текст')
        self.assertEqual(message.status, OutboundMessage.Status.HELD)
        self.assertIsNotNone(outbox.enqueue('n', USER, 'x', dedup_key='k1'))
        self.assertIsNone(outbox.enqueue('n', USER, 'x', dedup_key='k1'))  # дубль не ставится
        none = outbox.send('n', None, 'нет чата')
        self.assertEqual(none.status, OutboundMessage.Status.NO_CHAT)

    def test_guarded_download_cannot_bypass(self):
        from apps.graduate_bot.bot import bot

        telegram_guard.clear_permits()
        with mock.patch('telebot.apihelper.download_file') as raw:
            with self.assertRaises(telegram_guard.TelegramBlocked):
                bot.download_file('secret/path.png')
            raw.assert_not_called()

    def test_telegram_errors_classified(self):
        from telebot.apihelper import ApiTelegramException

        from apps.tgbot import outbox

        def failing(code):
            bot = mock.Mock()
            bot.send_message.side_effect = ApiTelegramException(
                'sendMessage', mock.Mock(), {'error_code': code, 'description': f'err {code}'})
            return bot

        for code, status in ((403, 'blocked'), (429, 'retry'), (500, 'retry'), (400, 'failed')):
            with mock.patch('apps.tgbot.outbox._bot', return_value=failing(code)):
                self.assertEqual(outbox.send('n', USER, 'x').status, status)

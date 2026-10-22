"""Этап 8B: проектные группы и темы, подключение участников, задачи через бота,
уведомления, исправления этапа 7. Telegram API — только моки."""
import datetime
import itertools
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.common import telegram_guard
from apps.interns import freeze
from apps.interns.models import Intern, InternStatus
from apps.projects.fixtures_closure import head
from apps.projects.models import Project
from apps.projects.services import create_project
from apps.reports import weekly
from apps.reports.models import LeadWeeklyReport
from apps.tasks import workflow
from apps.tasks.models import TaskStatus, TaskSubmission
from apps.teams import staff
from apps.teams.models import TeamMember, TeamRole
from apps.tgbot import groups
from apps.tgbot.dispatcher import handle_update
from apps.tgbot.models import BindCode, GroupMembership, GroupTopic, OutboundMessage, TelegramGroup
from apps.training.models import Specialization

GROUP, OTHER_GROUP, FOREIGN = -1001, -1002, -1003
INTERN_CHAT, LEAD_CHAT = 6001, 6002
STAGE = dict(GP2_ENVIRONMENT='stage', TELEGRAM_OUTBOUND_MODE='allowlist',
             TELEGRAM_TEST_CHAT_IDS={str(INTERN_CHAT), str(LEAD_CHAT)},
             TELEGRAM_TEST_GROUP_IDS={str(GROUP), str(OTHER_GROUP)}, SALES_TELEGRAM_CHAT_ID='')
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 32
_ids = itertools.count(50000)
UserModel = get_user_model()


def group_msg(chat, text, user=1):
    return {'update_id': next(_ids), 'message': {
        'message_id': next(_ids), 'date': 0, 'text': text, 'chat': {'id': chat, 'type': 'supergroup', 'title': 'Чат'},
        'from': {'id': user, 'is_bot': False, 'first_name': 'Р'}}}


def private(chat, text='', **extra):
    message = {'message_id': next(_ids), 'date': 0, 'chat': {'id': chat, 'type': 'private'},
               'from': {'id': chat, 'is_bot': False, 'first_name': 'С'}, **extra}
    if text:
        message['text'] = text
    return {'update_id': next(_ids), 'message': message}


def callback(chat, data):
    return {'update_id': next(_ids), 'callback_query': {
        'id': str(next(_ids)), 'data': data, 'from': {'id': chat, 'is_bot': False, 'first_name': 'С'},
        'message': {'message_id': 1, 'date': 0, 'chat': {'id': chat, 'type': 'private'}}}}


class FakeBot:
    def __init__(self, *, forum=True, admin=True, topics=True):
        self.sent, self.topics, self.forum, self.admin, self.topics_ok = [], [], forum, admin, topics

    def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=len(self.sent))

    def get_chat(self, chat_id):
        return SimpleNamespace(is_forum=self.forum, title=f'Группа {chat_id}')

    def get_me(self):
        return SimpleNamespace(id=999)

    def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status='administrator' if self.admin else 'member', can_manage_topics=self.admin)

    def create_forum_topic(self, chat_id, name, icon_custom_emoji_id=None):
        self.topics.append((chat_id, name))
        self.icons = getattr(self, 'icons', {})
        self.icons[name] = icon_custom_emoji_id
        return SimpleNamespace(message_thread_id=100 + len(self.topics))

    def get_forum_topic_icon_stickers(self):
        return [{'emoji': e, 'custom_emoji_id': f'id-{e}'} for e in ('📣', '💻', '📺', '🎨', '📱', '🧪', '💼', '💬', '⚡️')]

    def edit_forum_topic(self, chat_id, thread_id, name=None, icon_custom_emoji_id=None):
        self.edited = getattr(self, 'edited', []) + [(chat_id, thread_id, icon_custom_emoji_id)]
        return True

    def answer_callback_query(self, *a, **k):
        return True

    def get_file(self, file_id):
        return SimpleNamespace(file_path=f'photos/{file_id}.jpg', file_size=len(self.content))

    def download_file(self, path):
        return self.content


@override_settings(**STAGE)
class Base(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.frontend = Specialization.objects.create(name='Frontend')
        self.project = create_project(Project(name='Групповой', recruitment_open=True))
        self.lead_user = UserModel.objects.create_user(username='g-lead', password='x', role='team_lead')
        self.lead = Intern.objects.create(full_name='Тимлид Групп', user=self.lead_user, specialization=self.backend,
                                          position='lead', staff_role='lead', staff_status='active',
                                          telegram_chat_id=LEAD_CHAT)
        staff.assign_lead(self.project, self.lead, self.head)
        self.bot = FakeBot()
        self.bot.content = PNG
        for target in ('apps.tgbot.outbox._bot', 'apps.tgbot.groups._bot'):
            patcher = mock.patch(target, return_value=self.bot)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_update(self, update):
        with self.captureOnCommitCallbacks(execute=True):
            return handle_update(update)

    def member(self, name, role=TeamRole.BACKEND, chat=None, project=None):
        intern = Intern.objects.create(full_name=name, specialization=self.backend, status=InternStatus.ACTIVE,
                                       telegram_chat_id=chat, telegram='@x')
        with self.captureOnCommitCallbacks(execute=True):
            TeamMember.objects.create(project=project or self.project, intern=intern, role=role, status='active')
        return intern

    def bound(self, chat=GROUP, project=None):
        code = groups.issue_code(self.head, project=project or self.project)
        self.assertEqual(self.run_update(group_msg(chat, f'/bind {code.code}')), 'bind:pending')
        group = TelegramGroup.objects.get(chat_id=chat)
        with self.captureOnCommitCallbacks(execute=True):
            return groups.confirm(group, self.head)


class BindingTests(Base):
    def test_bind_confirm_and_topics_once(self):
        self.member('Фронт Стажёр', role=TeamRole.FRONTEND)
        group = self.bound()
        self.assertEqual(group.status, TelegramGroup.Status.ACTIVE)
        self.assertEqual(sorted(t.key for t in group.topics.all()), ['announcements', 'backend', 'frontend'])
        self.assertTrue(all(t.message_thread_id for t in group.topics.all()))
        self.assertEqual(groups.sync_topics(group), [])  # повторно не создаёт
        topic = group.topics.get(key='frontend')
        groups.mark_topic_missing(group, topic.message_thread_id)
        made = groups.sync_topics(group)  # удалённую — заново
        self.assertEqual([t.key for t in made], ['frontend'])
        self.assertEqual(GroupTopic.objects.filter(group=group).count(), 3)

    def test_codes_rights_and_conflicts(self):
        other = create_project(Project(name='Другой'))
        with self.assertRaises(PermissionDenied):
            groups.issue_code(self.lead_user, project=self.project)
        code = groups.issue_code(self.head, project=self.project)
        self.assertEqual(self.run_update(group_msg(GROUP, '/bind GP-WRONG')), 'bind:bad_code')
        BindCode.objects.filter(pk=code.pk).update(expires_at=timezone.now() - datetime.timedelta(minutes=1))
        self.assertEqual(self.run_update(group_msg(GROUP, f'/bind {code.code}')), 'bind:bad_code')  # истёк
        self.bot.forum = False
        fresh = groups.issue_code(self.head, project=self.project)
        self.assertEqual(self.run_update(group_msg(GROUP, f'/bind {fresh.code}')), 'bind:no_topics')
        self.bot.forum, self.bot.admin = True, False
        self.assertEqual(self.run_update(group_msg(GROUP, f'/bind {fresh.code}')), 'bind:no_rights')
        self.bot.admin = True
        self.assertEqual(self.run_update(group_msg(GROUP, f'/bind {fresh.code}')), 'bind:pending')
        self.assertEqual(self.run_update(group_msg(GROUP, f'/bind {fresh.code}')), 'bind:bad_code')  # одноразовый
        with self.assertRaises(groups.GroupError):
            groups.issue_code(self.head, project=self.project)  # у проекта уже есть группа
        second = groups.issue_code(self.head, project=other)
        self.assertEqual(self.run_update(group_msg(GROUP, f'/bind {second.code}')), 'bind:conflict')  # та же группа
        self.assertEqual(self.run_update(group_msg(FOREIGN, f'/bind {second.code}')), 'skipped:not_allowed')
        group = TelegramGroup.objects.get(chat_id=GROUP)
        self.assertEqual(group.status, 'pending')  # без подтверждения руководителя тем нет
        self.assertEqual(self.bot.topics, [])
        with self.assertRaises(PermissionDenied):
            groups.confirm(group, self.lead_user)

    def test_service_groups_and_unbind(self):
        code = groups.issue_code(self.head, kind=TelegramGroup.Kind.LEADS)
        self.run_update(group_msg(OTHER_GROUP, f'/bind {code.code}'))
        group = TelegramGroup.objects.get(chat_id=OTHER_GROUP)
        groups.confirm(group, self.head)
        self.assertEqual(group.topics.count(), 0)  # служебная — без тем проекта
        with self.assertRaises(groups.GroupError):
            groups.issue_code(self.head, kind=TelegramGroup.Kind.LEADS)
        groups.unbind(group, self.head)
        groups.issue_code(self.head, kind=TelegramGroup.Kind.LEADS)  # после отвязки — снова можно

    def test_topic_guard_stage_and_production(self):
        with self.assertRaises(telegram_guard.TelegramBlocked):
            telegram_guard.check('createForumTopic', {'chat_id': str(FOREIGN)})
        telegram_guard.check('createForumTopic', {'chat_id': str(GROUP)})
        with override_settings(GP2_ENVIRONMENT='production', TELEGRAM_OUTBOUND_MODE='on'):
            with self.assertRaises(telegram_guard.TelegramBlocked):
                telegram_guard.check('createForumTopic', {'chat_id': str(GROUP)})  # не привязана
            self.bound()
            telegram_guard.check('createForumTopic', {'chat_id': str(GROUP)})


class MembershipTests(Base):
    def test_invite_join_and_access_end(self):
        self.bound()
        intern = self.member('Новый Участник', chat=INTERN_CHAT)
        access = GroupMembership.objects.get(member__intern=intern)
        self.assertEqual(access.status, GroupMembership.Status.TO_INVITE)
        self.assertTrue(OutboundMessage.objects.filter(kind='team_change', chat_id=LEAD_CHAT,
                                                       text__contains='Новый Участник').exists())
        self.assertTrue(OutboundMessage.objects.filter(kind='project_assigned', intern=intern).exists())
        groups.mark(access, self.lead_user, GroupMembership.Status.INVITED)
        access.refresh_from_db()
        self.assertEqual(access.status, GroupMembership.Status.INVITED)  # ссылка ≠ вступление
        stranger = UserModel.objects.create_user(username='g-x', password='x', role='team_lead')
        with self.assertRaises(PermissionDenied):
            groups.mark(access, stranger, GroupMembership.Status.JOINED)
        update = {'update_id': next(_ids), 'chat_member': {
            'chat': {'id': GROUP, 'type': 'supergroup'}, 'from': {'id': INTERN_CHAT},
            'new_chat_member': {'status': 'member', 'user': {'id': INTERN_CHAT}}, 'date': 0}}
        self.assertEqual(self.run_update(update), 'member:joined')
        access.refresh_from_db()
        self.assertEqual((access.status, access.join_source), (GroupMembership.Status.JOINED, 'telegram'))
        with self.captureOnCommitCallbacks(execute=True):
            freeze.freeze_by_head(intern, 'пауза', self.head)
        access.refresh_from_db()
        self.assertEqual(access.status, GroupMembership.Status.ACCESS_ENDED)  # тимлиду — удалить из группы
        self.assertTrue(OutboundMessage.objects.filter(kind='team_change', text__contains='удалите').exists())
        groups.mark(access, self.lead_user, GroupMembership.Status.REMOVED)
        self.assertTrue(GroupMembership.objects.filter(pk=access.pk).exists())  # история цела
        self.client.force_login(self.lead_user)
        self.assertEqual(self.client.get('/lead/telegram/').status_code, 200)
        self.client.force_login(self.head)
        self.assertContains(self.client.get('/telegram/'), 'Telegram-группы')


class TasksThroughBotTests(Base):
    def setUp(self):
        super().setUp()
        self.intern = self.member('Бот Исполнитель', chat=INTERN_CHAT)
        self.other = self.member('Второй Исполнитель')
        self.task = workflow.create_task(self.project, self.head, title='API логина', direction='backend')
        with self.captureOnCommitCallbacks(execute=True):
            workflow.assign(self.task, self.intern, self.head)
            workflow.assign(self.task, self.other, self.head)

    def test_full_cycle_in_bot(self):
        self.assertTrue(OutboundMessage.objects.filter(kind='task_assigned', intern=self.intern).exists())
        self.assertEqual(self.run_update(private(INTERN_CHAT, 'Мои задачи')), 'tasks:list')
        self.assertEqual(self.run_update(callback(INTERN_CHAT, f'task:{self.task.pk}')), 'tasks:card')
        self.assertEqual(self.run_update(callback(INTERN_CHAT, f'start:{self.task.pk}')), 'tasks:started')
        self.assertEqual(self.run_update(callback(INTERN_CHAT, f'submit:{self.task.pk}')), 'submit:ask_photo')
        self.assertEqual(self.run_update(private(INTERN_CHAT, 'текст вместо фото')), 'submit:need_photo')
        photo = [{'file_id': 'small', 'file_size': 10, 'width': 90}, {'file_id': 'big', 'file_size': 40, 'width': 800}]
        self.assertEqual(self.run_update(private(INTERN_CHAT, photo=photo)), 'submit:photo')
        with mock.patch('apps.graduate_bot.bot.bot', self.bot):
            outcome = self.run_update(private(INTERN_CHAT, 'Готово https://github.com/x/pr/1'))
        self.assertEqual(outcome, 'submit:done')
        submission = TaskSubmission.objects.get()
        self.assertEqual((submission.links, submission.comment, submission.source),
                         ('https://github.com/x/pr/1', 'Готово', 'telegram'))
        self.assertTrue(submission.screenshot.name.endswith('.png'))  # тип — по содержимому, не по имени
        self.assertNotIn('big', submission.screenshot.name)
        review = OutboundMessage.objects.get(kind='submission_review', chat_id=LEAD_CHAT)
        self.assertIn(f'/lead/{self.project.pk}/tasks/{self.task.pk}/', review.text)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.IN_PROGRESS)  # второй исполнитель ещё не сдал
        with self.captureOnCommitCallbacks(execute=True):
            workflow.review(submission, self.lead_user, approve=False, comment='Нет тестов')
        result = OutboundMessage.objects.get(kind='submission_result', intern=self.intern)
        self.assertIn('Нет тестов', result.text)

    def test_bad_files_and_foreign_task(self):
        foreign = workflow.create_task(self.project, self.head, title='Чужая', direction='backend')
        self.assertEqual(self.run_update(callback(INTERN_CHAT, f'submit:{foreign.pk}')), 'submit:denied')
        self.assertEqual(self.run_update(callback(INTERN_CHAT, f'task:{foreign.pk}')), 'tasks:denied')
        self.run_update(callback(INTERN_CHAT, f'submit:{self.task.pk}'))
        doc = {'file_id': 'doc1', 'file_size': 10, 'mime_type': 'image/png', 'file_name': '../../evil.exe'}
        self.run_update(private(INTERN_CHAT, document=doc))
        self.bot.content = b'MZ\x90\x00 not an image'
        with mock.patch('apps.graduate_bot.bot.bot', self.bot):
            self.assertEqual(self.run_update(private(INTERN_CHAT, 'Без комментария')), 'submit:refused')
        self.run_update(callback(INTERN_CHAT, f'submit:{self.task.pk}'))
        self.run_update(private(INTERN_CHAT, document=dict(doc, file_size=50 * 1024 * 1024)))
        with mock.patch('apps.graduate_bot.bot.bot', self.bot):
            self.assertEqual(self.run_update(private(INTERN_CHAT, 'Без комментария')), 'submit:refused')
        self.assertFalse(TaskSubmission.objects.exists())

    def test_release_of_one_assignee_keeps_true_status(self):
        workflow.start_work(self.task.pk, self.other)  # второй работает
        member = TeamMember.objects.get(project=self.project, intern=self.intern, status='active')
        with self.captureOnCommitCallbacks(execute=True):
            from apps.teams.services import leave_team

            leave_team(member)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.IN_PROGRESS)  # не ложное «К выполнению»
        self.assertTrue(self.task.needs_reassign)


class StageSevenFixTests(Base):
    def test_two_directions_two_obligations(self):
        today = datetime.date(2026, 10, 21)
        staff.assign_lead(self.project, self.lead, self.head, direction='frontend')
        self.assertEqual(staff.assign_lead(self.project, self.lead, self.head, direction='frontend')[1], False)
        TeamMember.objects.filter(intern=self.lead).update(joined_at=datetime.date(2026, 9, 1))
        from apps.dashboard.metrics import week_of

        week = week_of(today, today)
        items = weekly.lead_obligations(week, today, lead=self.lead)
        self.assertEqual(sorted(o.direction for o in items), ['backend', 'frontend'])
        with self.assertRaises(weekly.ReportError):
            weekly.submit_lead_report(self.lead_user, self.project, 'без направления', today=today)
        weekly.submit_lead_report(self.lead_user, self.project, 'по фронту', direction='frontend', today=today)
        status = {o.direction: o.status for o in weekly.lead_obligations(week, today, lead=self.lead)}
        self.assertEqual(status, {'backend': 'pending', 'frontend': 'submitted'})
        with self.assertRaises(PermissionDenied):
            weekly.submit_lead_report(self.lead_user, self.project, 'чужое', direction='qa', today=today)
        self.assertEqual(LeadWeeklyReport.objects.count(), 1)

    def test_staff_changes_and_reminders_are_notified(self):
        pm_user = UserModel.objects.create_user(username='g-pm', password='x', role='pm')
        pm_spec = Specialization.objects.create(name='PM')
        pm = Intern.objects.create(full_name='ПМ Уведомлений', user=pm_user, specialization=pm_spec,
                                   status=InternStatus.ACTIVE, telegram_chat_id=INTERN_CHAT)
        with self.captureOnCommitCallbacks(execute=True):
            staff.assign_pm(self.project, pm, self.head)
        self.assertTrue(OutboundMessage.objects.filter(kind='staff_assignment', intern=pm).exists())
        with self.captureOnCommitCallbacks(execute=True):
            weekly.remind(datetime.date(2026, 10, 23))
            weekly.remind(datetime.date(2026, 10, 23))
        self.assertEqual(OutboundMessage.objects.filter(kind='report_reminder', intern=self.lead).count(), 1)

    def test_no_telegram_and_stage_hold(self):
        silent = Intern.objects.create(full_name='Без Телеграма', specialization=self.backend)
        from apps.tgbot import notify

        with self.captureOnCommitCallbacks(execute=True):
            message = notify.to_intern(silent, 'x', 'текст')
        self.assertEqual(message.status, OutboundMessage.Status.NO_CHAT)
        with override_settings(TELEGRAM_OUTBOUND_MODE='off'):
            with mock.patch('apps.tgbot.outbox._bot') as real:
                real.return_value.send_message.return_value = None  # GuardedTeleBot не отправил
                with self.captureOnCommitCallbacks(execute=True):
                    held = notify.to_intern(self.lead, 'x', 'текст', dedup_key='hold-1')
        held.refresh_from_db()
        self.assertEqual(held.status, OutboundMessage.Status.HELD)

    def test_screenshot_validation_unit(self):
        from apps.tgbot.files import FileRejected, sniff

        self.assertEqual(sniff(PNG)[0], 'png')
        with self.assertRaises(FileRejected):
            sniff(b'<?php evil')
        self.assertIsInstance(SimpleUploadedFile('a.png', PNG), SimpleUploadedFile)


class Stage11FGroupTests(Base):
    """Иконки тем и команды в группах только от руководителя."""

    def setUp(self):
        super().setUp()
        from apps.tgbot import groups as g

        g._icon_ids.clear()
        self.addCleanup(g._icon_ids.clear)

    def test_topics_created_with_icons_and_existing_get_icons_without_recreate(self):
        self.member('Фронт Стажёр', role=TeamRole.FRONTEND)
        group = self.bound()
        bot = self.bot
        self.assertEqual(bot.icons['Объявления'], 'id-📣')
        self.assertEqual(bot.icons['Frontend'], 'id-📺')
        created = len(bot.topics)
        self.assertEqual(groups.apply_icons(group), GroupTopic.objects.filter(group=group).count())
        self.assertEqual(len(bot.topics), created)  # темы не пересоздаются

    @override_settings(TELEGRAM_GROUP_ADMIN_IDS={'777'})
    def test_group_commands_only_from_admin(self):
        code = groups.issue_code(self.head, project=self.project)
        self.assertEqual(self.run_update(group_msg(GROUP, f'/bind {code.code}', user=5)), 'group:cmd_denied')
        code.refresh_from_db()
        self.assertIsNone(code.used_at)  # чужая команда код не тратит
        self.assertFalse(TelegramGroup.objects.exists())
        self.assertEqual(self.run_update(group_msg(GROUP, f'/bind@GeeksProAutoBot {code.code}', user=777)), 'bind:pending')

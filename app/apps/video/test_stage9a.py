"""Этап 9A: создание собраний, права, приглашения, персональные ссылки, JaaS JWT."""
import base64
import datetime
import json

import rsa
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.interns.models import Intern, InternStatus
from apps.projects import closure
from apps.projects.fixtures_closure import head, make_ready
from apps.projects.models import Project
from apps.projects.services import create_project
from apps.teams import staff
from apps.teams.models import TeamMember, TeamRole
from apps.tgbot.models import OutboundMessage
from apps.training.models import Specialization
from apps.video import services
from apps.video.models import VideoMeeting
from apps.video.providers.jaas import JaasProvider

UserModel = get_user_model()
SIM = dict(VIDEO_PROVIDER='simulator', VIDEO_SIMULATOR_SECRET='sim-secret', SITE_URL='https://control.test')


def soon(hours=2):
    return timezone.now() + datetime.timedelta(hours=hours)


@override_settings(**SIM)
class Base(TestCase):
    def setUp(self):
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.frontend = Specialization.objects.create(name='Frontend')
        self.project = create_project(Project(name='Видео', recruitment_open=True))
        self.second = create_project(Project(name='Второй видео', recruitment_open=True))
        self.lead_user, self.lead = self.make_lead('v-lead', 'Тимлид Видео')
        for project in (self.project, self.second):
            staff.assign_lead(project, self.lead, self.head)
        self.front_user, self.front_lead = self.make_lead('v-front', 'Тимлид Фронта', self.frontend)
        staff.assign_lead(self.project, self.front_lead, self.head)
        self.pm_user = UserModel.objects.create_user(username='v-pm', password='x', role='pm')
        self.pm = Intern.objects.create(full_name='ПМ Видео', user=self.pm_user)
        TeamMember.objects.create(project=self.project, intern=self.pm, role=TeamRole.PROJECT_MANAGER, status='active')
        self.a = self.member('Стажёр А', chat=7101)
        self.b = self.member('Стажёр Б')
        self.f = self.member('Стажёр Ф', role=TeamRole.FRONTEND, spec=self.frontend)

    def make_lead(self, username, name, spec=None):
        user = UserModel.objects.create_user(username=username, password='x', role='team_lead')
        lead = Intern.objects.create(full_name=name, user=user, specialization=spec or self.backend, position='lead',
                                     staff_role='lead', staff_status='active')
        return user, lead

    def member(self, name, role=TeamRole.BACKEND, spec=None, chat=None, project=None):
        intern = Intern.objects.create(full_name=name, specialization=spec or self.backend,
                                       status=InternStatus.ACTIVE, telegram_chat_id=chat)
        TeamMember.objects.create(project=project or self.project, intern=intern, role=role, status='active')
        return intern

    def meeting(self, user=None, **kw):
        kw.setdefault('kind', VideoMeeting.Kind.REQUIRED)
        kw.setdefault('direction', 'backend')
        kw.setdefault('starts_at', soon())
        kw.setdefault('topic', 'Планёрка')
        kw.setdefault('intern_ids', [self.a.pk, self.b.pk])
        with self.captureOnCommitCallbacks(execute=True):
            return services.create_meeting(user or self.lead_user, kw.pop('project', self.project), **kw)


class CreateAndRightsTests(Base):
    def test_lead_creates_only_own_direction(self):
        meeting = self.meeting()
        self.assertEqual((meeting.host, meeting.direction, meeting.provider), (self.lead, 'backend', 'simulator'))
        self.assertEqual(meeting.week_start, timezone.localtime(meeting.starts_at).date()
                         - datetime.timedelta(days=timezone.localtime(meeting.starts_at).weekday()))
        with self.assertRaises(PermissionDenied):
            self.meeting(direction='frontend', intern_ids=[self.f.pk])  # чужое направление
        with self.assertRaises(services.MeetingError):
            self.meeting(intern_ids=[self.f.pk])  # участник другого направления
        with self.assertRaises(PermissionDenied):
            self.meeting(user=self.pm_user)  # ПМ не ведёт собрания направлений
        with self.assertRaises(PermissionDenied):
            self.meeting(user=self.head)  # руководитель видит, но не ведёт
        second = self.meeting(project=self.second, intern_ids=[self.member('Стажёр В', project=self.second).pk])
        self.assertEqual(second.project, self.second)  # тимлид двух проектов — свои собрания в каждом

    def test_pm_meeting_with_leads_is_not_required(self):
        meeting = self.meeting(user=self.pm_user, kind=VideoMeeting.Kind.PM_LEADS, direction='',
                               intern_ids=[self.lead.pk, self.front_lead.pk])
        self.assertEqual((meeting.kind, meeting.direction), ('pm_leads', ''))
        with self.assertRaises(services.MeetingError):
            self.meeting(user=self.pm_user, kind=VideoMeeting.Kind.PM_LEADS, direction='', intern_ids=[self.a.pk])
        with self.assertRaises(PermissionDenied):
            self.meeting(kind=VideoMeeting.Kind.PM_LEADS, direction='', intern_ids=[self.front_lead.pk])

    def test_closed_project_and_validation(self):
        with self.assertRaises(services.MeetingError):
            self.meeting(topic=' ')
        with self.assertRaises(services.MeetingError):
            self.meeting(duration_minutes=5)
        make_ready(self.project, self.head)
        closure.close_project(self.project, self.head)
        self.project.refresh_from_db()
        with self.assertRaises(services.MeetingError):
            self.meeting()

    def test_views_isolation(self):
        meeting = self.meeting()
        self.client.force_login(self.front_user)
        self.assertEqual(self.client.get(reverse('lead_portal:meeting_detail', args=[meeting.pk])).status_code, 404)
        self.client.force_login(self.lead_user)
        page = self.client.get(reverse('lead_portal:meeting_detail', args=[meeting.pk]))
        self.assertContains(page, 'Планёрка')
        self.assertNotContains(page, meeting.participants.first().join_token)  # чужие ссылки не показываем
        self.assertNotContains(page, 'gp-legacy')
        self.assertEqual(self.client.get(reverse('lead_portal:meeting_new')).status_code, 200)
        self.client.force_login(self.pm_user)
        self.assertContains(self.client.get(reverse('pm_portal:meeting_list')), 'Планёрка')
        other_pm = UserModel.objects.create_user(username='v-pm2', password='x', role='pm')
        Intern.objects.create(full_name='Чужой ПМ', user=other_pm)
        self.client.force_login(other_pm)
        self.assertNotContains(self.client.get(reverse('pm_portal:meeting_list')), 'Планёрка')
        self.client.force_login(self.head)
        page = self.client.get(reverse('video:list'), {'direction': 'backend'})
        self.assertContains(page, 'Планёрка')

    def test_cancel_reschedule_mark_held(self):
        meeting = self.meeting()
        with self.assertRaises(PermissionDenied):
            services.cancel(meeting, self.front_user, 'не моя')
        services.reschedule(meeting, self.lead_user, soon(30))
        with self.assertRaises(services.MeetingError):
            services.mark_held(meeting, self.lead_user, 'ещё рано')  # не началось
        VideoMeeting.objects.filter(pk=meeting.pk).update(starts_at=timezone.now() - datetime.timedelta(hours=1))
        with self.assertRaises(services.MeetingError):
            services.mark_held(meeting, self.lead_user, '')
        services.mark_held(meeting, self.lead_user, 'видеосервис упал, провели в Telegram')
        meeting.refresh_from_db()
        self.assertEqual((meeting.status, meeting.held_source), ('held', 'manual'))
        with self.assertRaises(services.MeetingError):
            services.cancel(meeting, self.lead_user, 'поздно')


class InvitesAndLinksTests(Base):
    def test_invites_only_to_current_members(self):
        meeting = self.meeting()
        invites = OutboundMessage.objects.filter(kind='meeting_invite')
        self.assertEqual(invites.count(), 2)
        text = invites.get(intern=self.a).text
        for part in ('Планёрка', 'Видео', 'Backend'):
            self.assertIn(part, text)
        self.assertNotIn('http', text)  # этап 10A: вход — кнопкой бота, не ссылкой
        self.assertEqual(invites.get(intern=self.a).reply_markup['inline_keyboard'][0][0]['callback_data'],
                         f'meetjoin:{meeting.participants.get(intern=self.a).pk}')
        self.assertEqual(invites.get(intern=self.b).status, OutboundMessage.Status.NO_CHAT)  # бот не запускал
        TeamMember.objects.filter(intern=self.b).update(status='left')
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(services.send_invitations(meeting), 1)  # вышедшему — не шлём

    def test_personal_link_window_and_membership(self):
        meeting = self.meeting(starts_at=timezone.now() + datetime.timedelta(minutes=30))
        part = meeting.participants.get(intern=self.a)
        response = self.client.get(reverse('video:join', args=[part.join_token]))
        # этап 10A: ссылка из приглашения больше не выдаёт JWT — только инструкция
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(part.provider_user_id, response.content.decode())
        part.refresh_from_db()
        self.assertIsNotNone(part.link_opened_at)
        self.assertFalse(part.events.exists())  # открытая ссылка — не посещение
        self.assertEqual(self.client.get(reverse('video:join', args=['bad-token'])).status_code, 403)
        TeamMember.objects.filter(intern=self.a).update(status='left')
        self.assertEqual(self.client.get(reverse('video:join', args=[part.join_token])).status_code, 403)
        VideoMeeting.objects.filter(pk=meeting.pk).update(starts_at=timezone.now() + datetime.timedelta(days=2))
        TeamMember.objects.filter(intern=self.a).update(status='active')
        self.assertEqual(self.client.get(reverse('video:join', args=[part.join_token])).status_code, 409)
        with self.assertRaises(PermissionDenied):
            services.host_join_url(meeting, self.front_user)


class JaasTokenTests(Base):
    def test_jwt_claims_follow_documentation(self):
        import tempfile

        _pub, priv = rsa.newkeys(1024)
        with tempfile.NamedTemporaryFile('w', suffix='.pem', delete=False) as handle:
            handle.write(priv.save_pkcs1().decode())
        meeting = self.meeting()
        with override_settings(JAAS_APP_ID='vpaas-magic-cookie-test', JAAS_KEY_ID='kid1',
                               JAAS_PRIVATE_KEY_PATH=handle.name, JAAS_WEBHOOK_SECRET='whsec_x'):
            provider = JaasProvider()
            self.assertTrue(provider.configured())
            url = provider.join_url(meeting, user_id='gp2u-abc', name='Стажёр А')
        self.assertTrue(url.startswith(f'https://8x8.vc/vpaas-magic-cookie-test/{meeting.room}?jwt='))
        token = url.split('jwt=', 1)[1]
        decode = lambda part: json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))  # noqa: E731
        header, payload = decode(token.split('.')[0]), decode(token.split('.')[1])
        self.assertEqual((header['alg'], header['kid']), ('RS256', 'vpaas-magic-cookie-test/kid1'))
        self.assertEqual((payload['aud'], payload['iss'], payload['sub'], payload['room']),
                         ('jitsi', 'chat', 'vpaas-magic-cookie-test', meeting.room))
        self.assertEqual(payload['context']['user'], {'id': 'gp2u-abc', 'name': 'Стажёр А', 'moderator': 'false'})
        self.assertLess(payload['nbf'], payload['exp'])

    def test_not_configured_jaas_refuses(self):
        meeting = self.meeting()
        with override_settings(JAAS_APP_ID='', JAAS_KEY_ID='', JAAS_PRIVATE_KEY_PATH='', JAAS_WEBHOOK_SECRET=''):
            provider = JaasProvider()
            self.assertFalse(provider.configured())
            with self.assertRaises(Exception):
                provider.join_url(meeting, user_id='x', name='y')
            self.assertFalse(provider.verify_webhook('t=1,v1=abc', b'{}'))

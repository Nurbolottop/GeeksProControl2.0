"""11F: второй проект параллельно — заявка из бота, решает руководитель.
Можно старым стажёрам и дизайнерам (UX/UI), новым остальным — нет."""
from unittest import mock

from django.test import override_settings

from apps.interns import freeze
from apps.interns.models import Intern, InternshipRequest, InternStatus
from apps.projects.models import Project
from apps.projects.services import create_project
from apps.teams.models import TeamMember, TeamRole
from apps.tgbot import menus
from apps.tgbot.models import OutboundMessage
from apps.tgbot.test_stage8a import STAGE, USER, Base, cb, msg
from apps.training.models import Specialization


@override_settings(**STAGE)
class SecondProjectTests(Base):
    def setUp(self):
        super().setUp()
        self.uxui = Specialization.objects.create(name='UX/UI')
        self.first = create_project(Project(name='Первый', recruitment_open=True))
        self.second = create_project(Project(name='Второй', recruitment_open=True, intern_brief='Мобильное приложение'))

    def intern(self, spec, role):
        intern = Intern.objects.create(full_name='Параллельный Стажёр', specialization=spec,
                                       status=InternStatus.ACTIVE, telegram_chat_id=USER)
        TeamMember.objects.create(project=self.first, intern=intern, role=role, status='active')
        return intern

    def test_designer_requests_second_project_and_head_approves(self):
        intern = self.intern(self.uxui, TeamRole.UXUI)
        self.assertIn(menus.AVAILABLE, menus.allowed_labels(intern, USER))
        self.assertEqual(self.send(msg(USER, menus.AVAILABLE)), 'second:list')
        self.assertEqual(self.send(cb(USER, f'second:{self.first.pk}')), 'second:unavailable')  # уже там
        self.assertEqual(self.send(cb(USER, f'second:{self.second.pk}')), 'second:confirm')
        self.assertEqual(self.send(cb(USER, f'second_ok:{self.second.pk}')), 'second:sent')
        req = InternshipRequest.objects.get(intern=intern, kind='second')
        self.assertEqual((req.status, req.chosen_project), ('pending', self.second))
        self.assertIn('«Второй»', req.reason)
        self.assertFalse(TeamMember.objects.filter(intern=intern, project=self.second).exists())  # до одобрения — нет
        self.assertEqual(self.send(msg(USER, menus.AVAILABLE)), 'second:pending')
        with self.captureOnCommitCallbacks(execute=True):
            freeze.approve(req, self.head)
        self.assertTrue(TeamMember.objects.filter(intern=intern, project=self.second, status='active').exists())
        self.assertTrue(TeamMember.objects.filter(intern=intern, project=self.first, status='active').exists())
        self.assertTrue(OutboundMessage.objects.filter(kind='request_decided', intern=intern,
                                                       text__contains='втором проекте «Второй»').exists())
        self.assertFalse(freeze.second_allowed(intern))  # уже в двух — третьего нет

    def test_new_backend_cannot_request_second(self):
        intern = self.intern(self.backend, TeamRole.BACKEND)
        self.assertFalse(freeze.second_allowed(intern))
        self.assertNotIn(menus.AVAILABLE, menus.allowed_labels(intern, USER))
        self.assertEqual(self.send(cb(USER, f'second_ok:{self.second.pk}')), 'cb:denied')
        self.assertFalse(InternshipRequest.objects.filter(kind='second').exists())

    def test_old_intern_can_request_second(self):
        intern = self.intern(self.backend, TeamRole.BACKEND)
        with mock.patch('apps.dashboard.graduation.compute', return_value=mock.Mock(ids={intern.pk})):
            self.assertTrue(freeze.second_allowed(intern))
            self.assertEqual([p.name for p in freeze.second_projects(intern)], ['Второй'])

    def test_rejected_request_does_not_add(self):
        intern = self.intern(self.uxui, TeamRole.UXUI)
        req = freeze.request_second(intern, self.second)
        with self.captureOnCommitCallbacks(execute=True):
            freeze.reject(req, self.head, 'пока рано')
        self.assertFalse(TeamMember.objects.filter(intern=intern, project=self.second).exists())
        self.assertTrue(OutboundMessage.objects.filter(kind='request_decided', intern=intern,
                                                       text__contains='отклонена').exists())

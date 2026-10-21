from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from apps.accounts.models import User
from apps.interns.models import Intern, InternStatus
from apps.projects.models import Project
from apps.teams.models import TeamMember, TeamRole

Model = get_user_model()


class LeadProjectOwnershipTests(TestCase):
    """Тимлид видит только тот проект, где сам активный
    TeamMember(role='team_lead')."""

    def setUp(self):
        from apps.training.models import Specialization

        self.backend_spec = Specialization.objects.create(name="Backend")
        self.lead_user = Model.objects.create_user(
            username="+996700000030", password="x", role=User.Role.TEAM_LEAD,
        )
        self.lead_intern = Intern.objects.create(
            full_name="Тестов Тимлид", user=self.lead_user,
            specialization=self.backend_spec,
        )
        self.project_a = Project.objects.create(name="Проект A", recruitment_open=True)
        self.project_b = Project.objects.create(name="Проект B")
        TeamMember.objects.create(
            project=self.project_a, intern=self.lead_intern, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        self.client.force_login(self.lead_user)

    def test_dashboard_lists_only_own_project(self):
        response = self.client.get(reverse("lead_portal:dashboard"))
        names = [p.name for p in response.context["projects"]]
        self.assertEqual(names, ["Проект A"])

    def test_dashboard_status_filter(self):
        from apps.projects.models import ProjectStatus

        self.project_a.status = ProjectStatus.COMPLETED
        self.project_a.save(update_fields=["status"])

        response = self.client.get(reverse("lead_portal:dashboard"), {"status": "completed"})
        names = [p.name for p in response.context["projects"]]
        self.assertEqual(names, ["Проект A"])

        response = self.client.get(reverse("lead_portal:dashboard"), {"status": "in_progress"})
        names = [p.name for p in response.context["projects"]]
        self.assertEqual(names, [])

    def test_can_open_own_project(self):
        response = self.client.get(
            reverse("lead_portal:project_detail", args=[self.project_a.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Проект A")

    def test_cannot_open_foreign_project(self):
        response = self.client.get(
            reverse("lead_portal:project_detail", args=[self.project_b.pk]),
        )
        self.assertEqual(response.status_code, 404)

    def test_left_membership_does_not_grant_access(self):
        TeamMember.objects.create(
            project=self.project_b, intern=self.lead_intern, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.LEFT,
        )
        response = self.client.get(
            reverse("lead_portal:project_detail", args=[self.project_b.pk]),
        )
        self.assertEqual(response.status_code, 404)

    def test_non_lead_role_on_project_does_not_grant_access(self):
        """Например, бэкендер на проекте — не тимлид, доступа нет."""
        TeamMember.objects.create(
            project=self.project_b, intern=self.lead_intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        response = self.client.get(
            reverse("lead_portal:project_detail", args=[self.project_b.pk]),
        )
        self.assertEqual(response.status_code, 404)

    def test_overview_tab_has_no_client_or_report_links(self):
        """Клиент и отчёты — не в компетенции тимлида. Документы проекта
        у тимлида есть с UI-03B (своя вкладка)."""
        response = self.client.get(
            reverse("lead_portal:project_detail", args=[self.project_a.pk]),
        )
        self.assertNotContains(response, "Клиент")
        # Отчёты по проекту (ProjectReport) — у ПМ и руководителя; свой
        # еженедельный отчёт тимлид сдаёт в «Отчёты и оценки» (этап 7B)
        self.assertNotContains(response, "?tab=report")
        self.assertNotContains(response, "/report/")
        self.assertContains(response, "?tab=documents")


class LeadTeamManagementTests(LeadProjectOwnershipTests):
    """Команда: тимлид правит и убирает людей своего проекта, ничего на
    чужом. Вручную добавить человека нельзя — новые стажёры приходят
    только по ссылке на анкету."""

    def test_no_manual_add_route(self):
        with self.assertRaises(NoReverseMatch):
            reverse("lead_portal:member_add", args=[self.project_a.pk])

    def test_team_tab_has_no_add_buttons(self):
        response = self.client.get(
            reverse("lead_portal:project_detail", args=[self.project_a.pk]) + "?tab=team",
        )
        self.assertNotContains(response, "+ Участник")
        self.assertNotContains(response, "+ Добавить")
        self.assertContains(response, "ссылке на анкету")

    def test_can_edit_member_on_own_project(self):
        intern = Intern.objects.create(full_name="Правим")
        member = TeamMember.objects.create(
            project=self.project_a, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        self.client.post(
            reverse("lead_portal:member_edit", args=[self.project_a.pk, member.pk]),
            {"intern": intern.pk, "status": "active", "comment": "правим тут"},
        )
        member.refresh_from_db()
        self.assertEqual(member.comment, "правим тут")

    def test_can_remove_member_from_own_project(self):
        intern = Intern.objects.create(full_name="Снимаемый")
        member = TeamMember.objects.create(
            project=self.project_a, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        self.client.post(
            reverse("lead_portal:member_delete", args=[self.project_a.pk, member.pk]),
            {"left_reason": "not_fit"},
        )
        member.refresh_from_db()
        self.assertEqual(member.status, TeamMember.Status.LEFT)
        self.assertEqual(member.left_reason, "not_fit")

    def test_team_tab_shows_only_own_project_members(self):
        intern = Intern.objects.create(full_name="Участник А")
        TeamMember.objects.create(
            project=self.project_a, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        response = self.client.get(
            reverse("lead_portal:project_detail", args=[self.project_a.pk]) + "?tab=team",
        )
        self.assertContains(response, "Участник А")

    def test_paused_intern_shows_badge_on_team_tab(self):
        intern = Intern.objects.create(
            full_name="Замороженный", status=InternStatus.PAUSED,
        )
        TeamMember.objects.create(
            project=self.project_a, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        response = self.client.get(
            reverse("lead_portal:project_detail", args=[self.project_a.pk]) + "?tab=team",
        )
        self.assertContains(response, "Приостановлен")

    def test_team_tab_shows_only_own_direction(self):
        """Тимлид по бэкенду видит только бэкенд-стажёров — ни ПМ, ни
        других направлений, ни других тимлидов."""
        backend_intern = Intern.objects.create(full_name="Бэкендер")
        frontend_intern = Intern.objects.create(full_name="Фронтендер")
        pm_intern = Intern.objects.create(full_name="Менеджер Проекта")
        TeamMember.objects.create(
            project=self.project_a, intern=backend_intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        TeamMember.objects.create(
            project=self.project_a, intern=frontend_intern, role=TeamRole.FRONTEND,
            status=TeamMember.Status.ACTIVE,
        )
        TeamMember.objects.create(
            project=self.project_a, intern=pm_intern, role=TeamRole.PROJECT_MANAGER,
            status=TeamMember.Status.ACTIVE,
        )
        response = self.client.get(
            reverse("lead_portal:project_detail", args=[self.project_a.pk]) + "?tab=team",
        )
        self.assertContains(response, "Бэкендер")
        self.assertNotContains(response, "Фронтендер")
        self.assertNotContains(response, "Менеджер Проекта")
        self.assertNotContains(response, "Project Manager")
        self.assertNotContains(response, "Frontend")

    def test_reassigning_graduate_to_active_slot_clears_graduate_status(self):
        """Тимлид меняет участника слота на выпускника, который «на
        проверке», — раньше это не снимало ему статус выпускника и не
        активировало его: он оставался «Готов к распределению» с уже
        реальным активным проектом в команде."""
        from apps.interns.models import GraduateStatus

        graduate = Intern.objects.create(
            full_name="Выпускник На Слоте", specialization=self.backend_spec,
            graduate_status=GraduateStatus.PENDING, status=InternStatus.READY,
        )
        member = TeamMember.objects.create(
            project=self.project_a, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        self.client.post(
            reverse("lead_portal:member_edit", args=[self.project_a.pk, member.pk]),
            {"intern": graduate.pk, "status": "active", "comment": ""},
        )
        graduate.refresh_from_db()
        self.assertEqual(graduate.graduate_status, "")
        self.assertEqual(graduate.status, InternStatus.ACTIVE)

    def test_cannot_edit_or_remove_member_of_other_direction(self):
        intern = Intern.objects.create(full_name="Чужое Направление")
        member = TeamMember.objects.create(
            project=self.project_a, intern=intern, role=TeamRole.FRONTEND,
            status=TeamMember.Status.ACTIVE,
        )
        response = self.client.post(
            reverse("lead_portal:member_edit", args=[self.project_a.pk, member.pk]),
            {"intern": intern.pk, "status": "active", "comment": "ой"},
        )
        self.assertEqual(response.status_code, 404)
        member.refresh_from_db()
        self.assertEqual(member.comment, "")

        response = self.client.post(
            reverse("lead_portal:member_delete", args=[self.project_a.pk, member.pk]),
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(TeamMember.objects.filter(pk=member.pk).exists())


class LeadAttendanceTests(TestCase):
    """Табель — только по группе своего проекта, полный доступ (как у ПМ)."""

    def setUp(self):
        from apps.flows.models import Flow, Group
        from apps.training.models import Specialization

        self.backend_spec = Specialization.objects.create(name="Backend")
        self.lead_user = Model.objects.create_user(
            username="+996700000040", password="x", role=User.Role.TEAM_LEAD,
        )
        self.lead_intern = Intern.objects.create(
            full_name="Тимлид Табельный", user=self.lead_user,
            specialization=self.backend_spec,
        )
        self.project_a = Project.objects.create(name="Проект С группой")
        self.project_b = Project.objects.create(name="Проект без доступа")
        flow = Flow.objects.create(number=1, status=Flow.Status.ACTIVE)
        self.group = Group.objects.create(flow=flow, number=1, project=self.project_a)
        TeamMember.objects.create(
            project=self.project_a, group=self.group, intern=self.lead_intern,
            role=TeamRole.TEAM_LEAD, status=TeamMember.Status.ACTIVE,
        )
        self.client.force_login(self.lead_user)

    def test_no_group_shows_empty_state(self):
        TeamMember.objects.create(
            project=self.project_b, intern=self.lead_intern, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        response = self.client.get(
            reverse("lead_portal:project_detail", args=[self.project_b.pk]) + "?tab=attendance",
        )
        self.assertContains(response, "ещё не назначена")

    def test_can_create_meeting_for_own_group(self):
        from apps.attendance.models import GroupMeeting

        self.client.post(
            reverse("lead_portal:meeting_create", args=[self.project_a.pk]),
            {"date": "2026-09-10"},
        )
        self.assertTrue(GroupMeeting.objects.filter(group=self.group).exists())

    def test_can_mark_attendance_and_score_activity(self):
        from apps.attendance import services as attendance_services
        from apps.attendance.models import MeetingKind, WorkScore

        member_intern = Intern.objects.create(full_name="Отмечаемый")
        TeamMember.objects.create(
            project=self.project_a, group=self.group, intern=member_intern,
            role=TeamRole.BACKEND, status=TeamMember.Status.ACTIVE,
        )
        meeting = attendance_services.create_meeting(
            self.group, kind=MeetingKind.INTERNAL,
            date=__import__("datetime").date(2026, 9, 10),
        )
        self.client.post(
            reverse(
                "lead_portal:meeting_mark_toggle",
                args=[self.project_a.pk, meeting.pk],
            ),
            {"intern": member_intern.pk},
        )
        self.client.post(
            reverse("lead_portal:meeting_score", args=[self.project_a.pk, meeting.pk]),
            {"intern": member_intern.pk, "score": "8"},
        )
        self.assertTrue(
            WorkScore.objects.filter(meeting=meeting, intern=member_intern, score=8).exists(),
        )

    def test_other_direction_member_not_visible_or_markable_on_meeting(self):
        """Бекенд-тимлид не видит и не может отмечать/оценивать фронтендера
        на собрании — только своё направление (как и в «Команде»)."""
        from apps.attendance import services as attendance_services
        from apps.attendance.models import Attendance, MeetingKind, WorkScore

        frontend_intern = Intern.objects.create(full_name="Фронтендер")
        TeamMember.objects.create(
            project=self.project_a, group=self.group, intern=frontend_intern,
            role=TeamRole.FRONTEND, status=TeamMember.Status.ACTIVE,
        )
        meeting = attendance_services.create_meeting(
            self.group, kind=MeetingKind.INTERNAL,
            date=__import__("datetime").date(2026, 9, 10),
        )

        detail_response = self.client.get(
            reverse("lead_portal:meeting_detail", args=[self.project_a.pk, meeting.pk]),
        )
        self.assertNotContains(detail_response, "Фронтендер")

        mark_response = self.client.post(
            reverse(
                "lead_portal:meeting_mark_toggle",
                args=[self.project_a.pk, meeting.pk],
            ),
            {"intern": frontend_intern.pk},
        )
        self.assertEqual(mark_response.status_code, 404)
        self.assertFalse(
            Attendance.objects.filter(meeting=meeting, intern=frontend_intern).exists(),
        )

        score_response = self.client.post(
            reverse("lead_portal:meeting_score", args=[self.project_a.pk, meeting.pk]),
            {"intern": frontend_intern.pk, "score": "8"},
        )
        self.assertEqual(score_response.status_code, 404)
        self.assertFalse(
            WorkScore.objects.filter(meeting=meeting, intern=frontend_intern).exists(),
        )

        self.client.post(
            reverse("lead_portal:meeting_mark_all", args=[self.project_a.pk, meeting.pk]),
        )
        self.assertFalse(
            Attendance.objects.filter(meeting=meeting, intern=frontend_intern).exists(),
        )

    def test_cannot_create_new_meeting_while_previous_incomplete(self):
        """Табель обязателен: нельзя создать новое собрание, пока не
        отмечены посещаемость и оценки всем стажёрам своего направления
        на предыдущем."""
        from apps.attendance import services as attendance_services
        from apps.attendance.models import GroupMeeting, MeetingKind

        member_intern = Intern.objects.create(full_name="Недооценённый")
        TeamMember.objects.create(
            project=self.project_a, group=self.group, intern=member_intern,
            role=TeamRole.BACKEND, status=TeamMember.Status.ACTIVE,
        )
        attendance_services.create_meeting(
            self.group, kind=MeetingKind.INTERNAL,
            date=__import__("datetime").date(2026, 9, 10),
        )
        self.client.post(
            reverse("lead_portal:meeting_create", args=[self.project_a.pk]),
            {"date": "2026-09-17"},
        )
        self.assertEqual(GroupMeeting.objects.filter(group=self.group).count(), 1)

    def test_can_create_new_meeting_after_previous_fully_closed(self):
        from apps.attendance import services as attendance_services
        from apps.attendance.models import (
            Attendance, GroupMeeting, MeetingKind, WorkScore,
        )

        member_intern = Intern.objects.create(full_name="Закрытый")
        TeamMember.objects.create(
            project=self.project_a, group=self.group, intern=member_intern,
            role=TeamRole.BACKEND, status=TeamMember.Status.ACTIVE,
        )
        meeting = attendance_services.create_meeting(
            self.group, kind=MeetingKind.INTERNAL,
            date=__import__("datetime").date(2026, 9, 10),
        )
        Attendance.objects.create(
            meeting=meeting, intern=member_intern, status=Attendance.Status.PRESENT,
        )
        WorkScore.objects.create(meeting=meeting, intern=member_intern, score=7)

        self.client.post(
            reverse("lead_portal:meeting_create", args=[self.project_a.pk]),
            {"date": "2026-09-17"},
        )
        self.assertEqual(GroupMeeting.objects.filter(group=self.group).count(), 2)

    def test_can_create_new_meeting_when_no_own_direction_members_yet(self):
        """Некого отмечать — блокировка не мешает, если в направлении
        тимлида пока вообще никого нет."""
        from apps.attendance import services as attendance_services
        from apps.attendance.models import GroupMeeting, MeetingKind

        attendance_services.create_meeting(
            self.group, kind=MeetingKind.INTERNAL,
            date=__import__("datetime").date(2026, 9, 10),
        )
        self.client.post(
            reverse("lead_portal:meeting_create", args=[self.project_a.pk]),
            {"date": "2026-09-17"},
        )
        self.assertEqual(GroupMeeting.objects.filter(group=self.group).count(), 2)

    def test_dashboard_and_tab_show_pending_meeting_indicator(self):
        from apps.attendance import services as attendance_services
        from apps.attendance.models import MeetingKind

        member_intern = Intern.objects.create(full_name="Неотмеченный")
        TeamMember.objects.create(
            project=self.project_a, group=self.group, intern=member_intern,
            role=TeamRole.BACKEND, status=TeamMember.Status.ACTIVE,
        )
        attendance_services.create_meeting(
            self.group, kind=MeetingKind.INTERNAL,
            date=__import__("datetime").date(2026, 9, 10),
        )
        dashboard_response = self.client.get(reverse("lead_portal:dashboard"))
        self.assertContains(dashboard_response, "Табель не закрыт")

        project_response = self.client.get(
            reverse("lead_portal:project_detail", args=[self.project_a.pk]),
        )
        self.assertContains(project_response, 'gp-tabs__count">!<')

    def test_lead_cannot_be_marked_or_scored(self):
        """Тимлид не отмечается и не оценивается — его нет в табеле
        собственного собрания."""
        from apps.attendance import services as attendance_services
        from apps.attendance.models import Attendance, MeetingKind, WorkScore

        meeting = attendance_services.create_meeting(
            self.group, kind=MeetingKind.INTERNAL,
            date=__import__("datetime").date(2026, 9, 10),
        )
        mark_response = self.client.post(
            reverse(
                "lead_portal:meeting_mark_toggle",
                args=[self.project_a.pk, meeting.pk],
            ),
            {"intern": self.lead_intern.pk},
        )
        self.assertEqual(mark_response.status_code, 404)
        self.assertFalse(
            Attendance.objects.filter(meeting=meeting, intern=self.lead_intern).exists(),
        )

        score_response = self.client.post(
            reverse("lead_portal:meeting_score", args=[self.project_a.pk, meeting.pk]),
            {"intern": self.lead_intern.pk, "score": "8"},
        )
        self.assertEqual(score_response.status_code, 404)
        self.assertFalse(
            WorkScore.objects.filter(meeting=meeting, intern=self.lead_intern).exists(),
        )

        detail_response = self.client.get(
            reverse("lead_portal:meeting_detail", args=[self.project_a.pk, meeting.pk]),
        )
        self.assertNotContains(detail_response, "Team Lead")

    def test_cannot_reach_meeting_from_foreign_group(self):
        from apps.flows.models import Flow, Group
        from apps.attendance import services as attendance_services
        from apps.attendance.models import MeetingKind

        TeamMember.objects.create(
            project=self.project_b, intern=self.lead_intern, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        flow = Flow.objects.create(number=2, status=Flow.Status.ACTIVE)
        foreign_group = Group.objects.create(flow=flow, number=1, project=None)
        meeting = attendance_services.create_meeting(
            foreign_group, kind=MeetingKind.INTERNAL,
            date=__import__("datetime").date(2026, 9, 10),
        )
        response = self.client.get(
            reverse(
                "lead_portal:meeting_detail",
                args=[self.project_a.pk, meeting.pk],
            ),
        )
        self.assertEqual(response.status_code, 404)


class LeadInternDetailTests(LeadProjectOwnershipTests):
    """Карточка стажёра — только по своему проекту, только чтение."""

    def test_can_view_own_project_intern(self):
        intern = Intern.objects.create(full_name="Видимый Стажёр")
        TeamMember.objects.create(
            project=self.project_a, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        response = self.client.get(
            reverse("lead_portal:intern_detail", args=[self.project_a.pk, intern.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Видимый Стажёр")

    def test_shows_total_projects_count(self):
        intern = Intern.objects.create(full_name="Считаемый Стажёр")
        TeamMember.objects.create(
            project=self.project_a, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        TeamMember.objects.create(
            project=self.project_b, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.LEFT,
        )
        response = self.client.get(
            reverse("lead_portal:intern_detail", args=[self.project_a.pk, intern.pk]),
        )
        self.assertEqual(response.context["projects_count"], 2)

    def test_cannot_view_foreign_project_intern(self):
        intern = Intern.objects.create(full_name="Чужой Стажёр")
        TeamMember.objects.create(
            project=self.project_b, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        response = self.client.get(
            reverse("lead_portal:intern_detail", args=[self.project_a.pk, intern.pk]),
        )
        self.assertEqual(response.status_code, 404)

    def test_cannot_view_intern_of_other_direction(self):
        intern = Intern.objects.create(full_name="Фронтендер")
        TeamMember.objects.create(
            project=self.project_a, intern=intern, role=TeamRole.FRONTEND,
            status=TeamMember.Status.ACTIVE,
        )
        response = self.client.get(
            reverse("lead_portal:intern_detail", args=[self.project_a.pk, intern.pk]),
        )
        self.assertEqual(response.status_code, 404)


class LeadPortalExcludedActionsTests(TestCase):
    """Статус/этап/дедлайн, отчёты, клиент — этих урлов в портале тимлида
    просто нет (в отличие от ПМ-портала). Документы — есть (UI-03B)."""

    def test_no_report_client_or_stage_urls(self):
        from django.urls import NoReverseMatch

        for name in (
            "lead_portal:report_create", "lead_portal:client_edit", "lead_portal:stage_set",
        ):
            with self.assertRaises(NoReverseMatch):
                reverse(name, args=[1])


class LeadResumeTests(TestCase):
    """Тимлид сам ведёт своё резюме в резерве кадров — под логином, без ссылок."""

    def setUp(self):
        from apps.reserve.models import CandidateStatus, ReserveCandidate

        self.lead_user = Model.objects.create_user(
            username="+996700000040", password="x", role=User.Role.TEAM_LEAD,
        )
        self.lead = Intern.objects.create(full_name="Резюмеев Тимлид", user=self.lead_user)
        self.project = Project.objects.create(name="Омур")
        TeamMember.objects.create(
            project=self.project, intern=self.lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        # тимлид попадает в резерв сам — берём его карточку и наполняем
        self.card = ReserveCandidate.objects.get(intern=self.lead)
        ReserveCandidate.objects.filter(pk=self.card.pk).update(
            full_name="Резюмеев Тимлид", phone="0700111222",
            skills="Python", status=CandidateStatus.RESERVE, rating=7,
            comment_pm="внутренний комментарий", consent_given=True,
        )
        self.card.refresh_from_db()
        self.url = reverse("lead_portal:resume")
        self.client.force_login(self.lead_user)

    def _payload(self, **extra):
        return {
            "full_name": "Резюмеев Тимлид", "phone": "0700111222",
            "skills": "Python, Django, Docker", "desired_position": "Team Lead",
            "consent_given": "on", **extra,
        }

    def test_lead_sees_own_resume_in_view_mode_first(self):
        """Открывается сначала на просмотр — форма редактирования не
        отдаётся, пока не нажали «Редактировать»."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["candidate"], self.card)
        self.assertFalse(response.context["editing"])
        self.assertContains(response, "Редактировать")
        self.assertNotContains(response, "Сохранить резюме")
        self.assertNotContains(response, "внутренний комментарий")

    def test_lead_can_switch_to_edit_mode(self):
        response = self.client.get(self.url, {"edit": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["editing"])
        self.assertContains(response, "Сохранить резюме")
        self.assertNotContains(response, "внутренний комментарий")

    def test_lead_updates_resume(self):
        response = self.client.post(self.url, self._payload())
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.card.refresh_from_db()
        self.assertEqual(self.card.skills, "Python, Django, Docker")
        self.assertEqual(self.card.desired_position, "Team Lead")
        self.assertIsNotNone(self.card.submitted_at)
        self.assertEqual(self.card.updated_by, self.lead_user)
        self.assertTrue(
            self.card.events.filter(title="Кандидат обновил резюме в своём портале").exists()
        )

    def test_lead_cannot_touch_internal_fields(self):
        self.client.post(self.url, self._payload(
            status="employed", rating="10", comment_pm="всё отлично", intern="",
        ))
        self.card.refresh_from_db()
        self.assertEqual(self.card.status, "reserve")
        self.assertEqual(self.card.rating, 7)
        self.assertEqual(self.card.comment_pm, "внутренний комментарий")
        self.assertEqual(self.card.intern, self.lead)

    def test_dashboard_shows_resume_card(self):
        response = self.client.get(reverse("lead_portal:dashboard"))
        self.assertContains(response, "Моё резюме в резерве кадров")

    def test_lead_without_card_gets_one_on_the_spot(self):
        """Карточку отвязали — открыв резюме, тимлид сразу получает новую."""
        from apps.reserve.models import ReserveCandidate

        self.card.intern = None
        self.card.phone = ""
        self.card.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        fresh = ReserveCandidate.objects.get(intern=self.lead)
        self.assertEqual(response.context["candidate"], fresh)
        self.assertNotEqual(fresh.pk, self.card.pk)

    def test_non_lead_without_card_sees_empty_state(self):
        other_user = Model.objects.create_user(
            username="+996700000043", password="x", role=User.Role.TEAM_LEAD,
        )
        Intern.objects.create(full_name="Не Тимлид", user=other_user)
        self.client.force_login(other_user)
        response = self.client.get(self.url)
        self.assertIsNone(response.context["candidate"])
        self.assertContains(response, "Вас пока нет в резерве кадров")

    def test_cannot_edit_someone_elses_resume(self):
        other_user = Model.objects.create_user(
            username="+996700000041", password="x", role=User.Role.TEAM_LEAD,
        )
        Intern.objects.create(full_name="Другой", user=other_user)
        self.client.force_login(other_user)
        self.client.post(self.url, self._payload(skills="чужое"))
        self.card.refresh_from_db()
        self.assertEqual(self.card.skills, "Python")

    def test_pm_is_kept_out_of_lead_resume(self):
        pm = Model.objects.create_user(
            username="+996700000042", password="x", role=User.Role.PROJECT_MANAGER,
        )
        self.client.force_login(pm)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_archived_lead_in_reserve_keeps_login_for_resume(self):
        from apps.interns.services import archive_person

        archive_person(self.lead)
        self.lead_user.refresh_from_db()
        self.assertTrue(self.lead_user.is_active)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        # но команды у него больше нет
        response = self.client.get(
            reverse("lead_portal:project_detail", args=[self.project.pk]),
        )
        self.assertEqual(response.status_code, 404)

    def test_archived_person_without_reserve_loses_login(self):
        """Без карточки в резерве вход при архивации закрывается (у тимлида
        карточка есть всегда, так что это про остальных людей)."""
        from apps.interns.services import archive_person

        self.card.delete()
        self.lead.refresh_from_db()
        archive_person(self.lead)
        self.lead_user.refresh_from_db()
        self.assertFalse(self.lead_user.is_active)

    def test_new_lead_goes_to_reserve_automatically(self):
        from apps.reserve.models import ReserveCandidate

        person = Intern.objects.create(
            full_name="Свежий Тимлид", phone="0700555666", telegram="@fresh",
        )
        self.assertFalse(ReserveCandidate.objects.filter(intern=person).exists())
        TeamMember.objects.create(
            project=self.project, intern=person, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        card = ReserveCandidate.objects.get(intern=person)
        self.assertEqual(card.full_name, "Свежий Тимлид")
        self.assertEqual(card.phone, "0700555666")
        self.assertEqual(card.status, "review")
        self.assertTrue(card.events.filter(title__contains="автоматически").exists())

    def test_promotion_to_lead_adds_to_reserve(self):
        from apps.reserve.models import ReserveCandidate

        person = Intern.objects.create(full_name="Бывший Бэкендер")
        member = TeamMember.objects.create(
            project=self.project, intern=person, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        self.assertFalse(ReserveCandidate.objects.filter(intern=person).exists())
        member.role = TeamRole.TEAM_LEAD
        member.save(update_fields=["role"])
        self.assertTrue(ReserveCandidate.objects.filter(intern=person).exists())

    def test_existing_unlinked_application_is_reused(self):
        from apps.reserve.models import ReserveCandidate

        application = ReserveCandidate.objects.create(
            full_name="Анкета Из Ссылки", phone="+996 700 999 000", skills="Go",
        )
        person = Intern.objects.create(full_name="Тимлид С Анкетой", phone="0700999000")
        TeamMember.objects.create(
            project=self.project, intern=person, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        application.refresh_from_db()
        self.assertEqual(application.intern, person)
        self.assertEqual(ReserveCandidate.objects.filter(intern=person).count(), 1)

    def test_backfill_command(self):
        from io import StringIO

        from django.core.management import call_command

        from apps.reserve.models import ReserveCandidate

        old = Intern.objects.create(full_name="Давний Тимлид")
        TeamMember.objects.bulk_create([TeamMember(
            project=self.project, intern=old, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )])  # в обход сигнала — как тимлиды, назначенные до этой логики
        out = StringIO()
        call_command("add_leads_to_reserve", stdout=out)
        self.assertFalse(ReserveCandidate.objects.filter(intern=old).exists())
        self.assertIn("завести: Давний Тимлид", out.getvalue())
        call_command("add_leads_to_reserve", apply=True, stdout=StringIO())
        self.assertTrue(ReserveCandidate.objects.filter(intern=old).exists())


class LeadProjectClosedNotificationTests(TestCase):
    """Проект завершён или закрыт — тимлиду приходит уведомление в портал."""

    def setUp(self):
        self.lead_user = Model.objects.create_user(
            username="+996700000050", password="x", role=User.Role.TEAM_LEAD,
        )
        self.lead = Intern.objects.create(full_name="Уведомлёнов Тимлид", user=self.lead_user)
        self.intern = Intern.objects.create(full_name="Просто Стажёр")
        self.old_lead = Intern.objects.create(full_name="Ушедший Тимлид")
        self.project = Project.objects.create(name="Омур")
        TeamMember.objects.create(
            project=self.project, intern=self.lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        TeamMember.objects.create(
            project=self.project, intern=self.intern, role=TeamRole.OTHER,
            status=TeamMember.Status.ACTIVE,
        )
        TeamMember.objects.create(
            project=self.project, intern=self.old_lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.LEFT,
        )
        self.client.force_login(self.lead_user)

    def _close(self, status):
        if status == "completed":
            # 2.0: «Завершён» — только процедура закрытия руководителем
            from apps.projects import closure
            from apps.projects.fixtures_closure import make_ready

            head = make_ready(self.project)
            closure.close_project(self.project, head)
            return
        from apps.projects.services import update_project

        self.project.status = status
        update_project(self.project, {"status": "active"})

    def test_completed_project_notifies_lead(self):
        from apps.notifications.models import Notification

        self._close("completed")
        note = Notification.objects.get(intern=self.lead)
        self.assertEqual(note.title, "Проект «Омур» завершён")
        self.assertEqual(note.level, "success")

    def test_cancelled_and_refused_projects_notify_lead(self):
        from apps.notifications.models import Notification

        self._close("cancelled")
        self.assertEqual(
            Notification.objects.get(intern=self.lead).title,
            "Проект «Омур» закрыт: отменён",
        )

    def test_only_active_leads_are_notified(self):
        from apps.notifications.models import Notification

        self._close("completed")
        self.assertFalse(Notification.objects.filter(intern=self.intern).exists())
        self.assertFalse(Notification.objects.filter(intern=self.old_lead).exists())

    def test_pause_does_not_notify(self):
        from apps.notifications.models import Notification

        self._close("paused")
        self.assertFalse(Notification.objects.filter(intern=self.lead).exists())

    def test_head_button_close_notifies_lead(self):
        """Закрытие кнопкой руководителя (принудительного закрытия в 2.0 нет)."""
        from apps.notifications.models import Notification
        from apps.projects.fixtures_closure import make_ready

        head = make_ready(self.project)
        self.client.force_login(head)
        self.client.post(reverse("projects:complete", args=[self.project.pk]))
        self.assertTrue(
            Notification.objects.filter(intern=self.lead, title__endswith="завершён").exists()
        )

    def test_personal_notification_stays_out_of_head_feed(self):
        from apps.notifications import services

        self._close("completed")
        # В ленте руководителя — только его собственное «Проект закрыт» (UI-03B),
        # личное уведомление тимлида туда не попадает
        self.assertFalse(services.feed().filter(title="Проект «Омур» завершён").exists())
        self.assertEqual(list(services.feed().values_list("title", flat=True)), ["Проект «Омур» закрыт"])

    def test_dashboard_shows_marks_read_and_close_hides(self):
        from apps.notifications.models import Notification

        self._close("completed")
        response = self.client.get(reverse("lead_portal:dashboard"))
        self.assertContains(response, "Проект «Омур» завершён")
        note = Notification.objects.get(intern=self.lead)
        self.assertTrue(note.is_read)
        self.client.post(reverse("lead_portal:notification_close", args=[note.pk]))
        response = self.client.get(reverse("lead_portal:dashboard"))
        self.assertNotContains(response, "Проект «Омур» завершён")

    def test_cannot_close_someone_elses_notification(self):
        from apps.notifications.services import notify

        note = notify("Чужое", intern=self.intern)
        self.client.post(reverse("lead_portal:notification_close", args=[note.pk]))
        note.refresh_from_db()
        self.assertFalse(note.is_closed)

    def test_notification_waits_for_login(self):
        """Логин выдали позже — уведомление уже ждёт в портале."""
        from apps.notifications.models import Notification

        late = Intern.objects.create(full_name="Без Логина")
        TeamMember.objects.create(
            project=self.project, intern=late, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        self._close("completed")
        self.assertTrue(Notification.objects.filter(intern=late).exists())
        user = Model.objects.create_user(
            username="+996700000051", password="x", role=User.Role.TEAM_LEAD,
        )
        late.user = user
        late.save()
        self.client.force_login(user)
        self.assertContains(
            self.client.get(reverse("lead_portal:dashboard")), "Проект «Омур» завершён",
        )



class LeadProfileLinkTests(LeadProjectOwnershipTests):
    """Тимлид сам выпускает ссылку на анкету для новых стажёров проекта."""

    def setUp(self):
        super().setUp()

        self.spec = self.backend_spec
        self.team_url = (
            reverse("lead_portal:project_detail", args=[self.project_a.pk]) + "?tab=team"
        )

    def _payload(self, **overrides):
        data = {
            "full_name": "Новый Бэкендер", "phone": "0700777888",
            "email": "new@example.com", "telegram": "@newbe",
            "city": "Бишкек", "branch": "Бишкек", "specialization": self.spec.pk,
            "education_end_date": "2026-06-01", "internship_start_date": "2026-07-01",
            "internship_attempt": "1",
        }
        data.update(overrides)
        return data

    def _create_link(self, project=None):
        project = project or self.project_a
        return self.client.post(
            reverse("lead_portal:profile_link_create", args=[project.pk]), {"ttl_days": "7"},
        )

    def test_lead_creates_link_bound_to_project(self):
        from apps.interns.models import ProfileFormLink

        response = self._create_link()
        self.assertRedirects(response, self.team_url, fetch_redirect_response=False)
        link = ProfileFormLink.objects.get(is_active=True, project=self.project_a)
        self.assertEqual(link.created_by, self.lead_user)
        self.assertIsNotNone(link.expires_at)
        page = self.client.get(self.team_url)
        self.assertContains(page, link.get_absolute_url())

    def test_cannot_create_link_for_foreign_project(self):
        from apps.interns.models import ProfileFormLink

        self.assertEqual(self._create_link(self.project_b).status_code, 404)
        self.assertFalse(ProfileFormLink.objects.filter(project=self.project_b).exists())

    def test_new_link_replaces_only_this_projects_link(self):
        from apps.interns import services as intern_services
        from apps.interns.models import ProfileFormLink

        general = intern_services.issue_profile_form_link()
        other = intern_services.issue_profile_form_link(project=self.project_b)
        self._create_link()
        first = ProfileFormLink.objects.get(is_active=True, project=self.project_a)
        self._create_link()
        first.refresh_from_db()
        general.refresh_from_db()
        other.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(general.is_active)
        self.assertTrue(other.is_active)

    def test_head_general_link_does_not_kill_project_links(self):
        from apps.interns import services as intern_services
        from apps.interns.models import ProfileFormLink

        self._create_link()
        intern_services.issue_profile_form_link()
        self.assertTrue(
            ProfileFormLink.objects.filter(is_active=True, project=self.project_a).exists()
        )

    def test_disable_link(self):
        from apps.interns.models import ProfileFormLink

        self._create_link()
        self.client.post(reverse("lead_portal:profile_link_disable", args=[self.project_a.pk]))
        self.assertFalse(
            ProfileFormLink.objects.filter(is_active=True, project=self.project_a).exists()
        )

    def test_filled_form_puts_person_into_project_team(self):
        from apps.interns.models import Intern as InternModel, ProfileFormLink
        from apps.notifications.models import Notification

        self._create_link()
        link = ProfileFormLink.objects.get(is_active=True, project=self.project_a)
        self.client.logout()
        response = self.client.post(
            reverse("intern_profile_apply", args=[link.token]), self._payload(),
        )
        self.assertEqual(response.status_code, 200)
        person = InternModel.objects.get(phone="0700777888")
        member = TeamMember.objects.get(project=self.project_a, intern=person)
        self.assertEqual(member.status, TeamMember.Status.ACTIVE)
        self.assertEqual(member.role, TeamRole.BACKEND)
        self.assertIsNotNone(member.joined_at)
        self.assertEqual(person.status, "active")
        note = Notification.objects.get(intern=self.lead_intern)
        self.assertIn("Новый Бэкендер", note.title)
        self.assertIn("Проект A", note.title)

    def test_refilled_form_does_not_duplicate_membership(self):
        from apps.interns.models import Intern as InternModel, ProfileFormLink

        self._create_link()
        link = ProfileFormLink.objects.get(is_active=True, project=self.project_a)
        self.client.logout()
        url = reverse("intern_profile_apply", args=[link.token])
        self.client.post(url, self._payload())
        self.client.session.flush()
        other = self.client_class()
        other.post(url, self._payload())
        person = InternModel.objects.get(phone="0700777888")
        self.assertEqual(
            TeamMember.objects.filter(project=self.project_a, intern=person).count(), 1,
        )

    def test_general_link_does_not_add_to_any_team(self):
        from apps.interns import services as intern_services
        from apps.interns.models import Intern as InternModel

        link = intern_services.issue_profile_form_link()
        self.client.logout()
        self.client.post(reverse("intern_profile_apply", args=[link.token]), self._payload())
        person = InternModel.objects.get(phone="0700777888")
        self.assertFalse(person.team_memberships.exists())


class LeadOverviewTests(TestCase):
    """«Обзор» тимлида: путь проекта, его направление в цифрах, кому нужно
    внимание, собрания и контакт ПМ."""

    def setUp(self):
        import datetime

        from django.utils import timezone

        from apps.attendance.models import Attendance, GroupMeeting, WorkScore
        from apps.flows.models import Flow, Group
        from apps.projects.services import create_project
        from apps.training.models import Specialization

        today = timezone.localdate()
        backend = Specialization.objects.create(name="Backend")
        frontend = Specialization.objects.create(name="Frontend")
        self.lead_user = Model.objects.create_user(
            username="+996700000060", password="x", role=User.Role.TEAM_LEAD,
        )
        self.lead = Intern.objects.create(
            full_name="Обзоров Тимлид", user=self.lead_user, specialization=backend,
        )
        self.project = Project.objects.create(
            name="Омур", planned_end_date=today + datetime.timedelta(days=10),
        )
        create_project(self.project)
        group = Group.objects.filter(project=self.project).first() or Group.objects.create(
            flow=Flow.objects.create(number=77), number=1, project=self.project,
        )
        self.pm = Intern.objects.create(full_name="Проектов ПМ", phone="0700111000", telegram="@pm")
        TeamMember.objects.create(project=self.project, intern=self.pm, role=TeamRole.PROJECT_MANAGER, status="active")
        TeamMember.objects.create(project=self.project, intern=self.lead, role=TeamRole.TEAM_LEAD, status="active")
        self.good = Intern.objects.create(full_name="Ходит Всегда", specialization=backend)
        self.absent = Intern.objects.create(full_name="Пропускает Часто", specialization=backend)
        self.front = Intern.objects.create(full_name="Фронт Чужой", specialization=frontend)
        for person, role in ((self.good, TeamRole.BACKEND), (self.absent, TeamRole.BACKEND), (self.front, TeamRole.FRONTEND)):
            TeamMember.objects.create(project=self.project, intern=person, role=role, status="active")
        for days in (14, 7):
            meeting = GroupMeeting.objects.create(
                group=group, date=today - datetime.timedelta(days=days), status="held",
            )
            Attendance.objects.create(meeting=meeting, intern=self.good, status="present")
            Attendance.objects.create(meeting=meeting, intern=self.absent, status="absent")
            Attendance.objects.create(meeting=meeting, intern=self.front, status="absent")
            WorkScore.objects.create(meeting=meeting, intern=self.good, score=9)
        self.unmarked = GroupMeeting.objects.create(
            group=group, date=today - datetime.timedelta(days=2), status="planned",
        )
        self.upcoming = GroupMeeting.objects.create(
            group=group, date=today + datetime.timedelta(days=3), status="planned",
        )
        self.client.force_login(self.lead_user)
        self.url = reverse("lead_portal:project_detail", args=[self.project.pk])

    def test_overview_shows_road_tiles_and_pm(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Путь проекта")
        self.assertContains(response, "ПМ проекта")
        self.assertContains(response, "@pm")
        labels = [tile["label"] for tile in response.context["lead"]["lead_tiles"]]
        self.assertEqual(labels, ["моё направление", "посещаемость", "средняя активность", "до дедлайна"])
        self.assertNotContains(response, "Кладётся внутрь")

    def test_only_own_direction_is_counted(self):
        tiles = {t["label"]: t for t in self.client.get(self.url).context["lead"]["lead_tiles"]}
        self.assertEqual(tiles["моё направление"]["value"], 2)
        # 2 из 4 отметок бэкенда — «был»; фронтенд в расчёт не идёт
        self.assertEqual(tiles["посещаемость"]["value"], 50)
        self.assertEqual(tiles["средняя активность"]["text"], "9,0")

    def test_attention_lists_people_who_slip(self):
        response = self.client.get(self.url)
        names = [row["person"].full_name for row in response.context["lead"]["attention"]]
        self.assertEqual(names, ["Пропускает Часто"])
        self.assertContains(response, "посещаемость 0%")
        self.assertNotContains(response, "Фронт Чужой")

    def test_meetings_block(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context["lead"]["unmarked"], [self.unmarked])
        self.assertEqual(response.context["lead"]["upcoming"], [self.upcoming])
        self.assertContains(response, "прошло, но не отмечено")

    def test_graphics_tab_in_main_app_has_no_template_leftovers(self):
        head = Model.objects.create_user(username="head-gx", password="x")
        self.client.force_login(head)
        response = self.client.get(f"{self.project.get_absolute_url()}?tab=graphics")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Путь проекта")
        self.assertNotContains(response, "Кладётся внутрь")

    def test_finished_project_shows_status_instead_of_deadline(self):
        from apps.projects.models import ProjectStatus

        Project.objects.filter(pk=self.project.pk).update(status=ProjectStatus.COMPLETED)
        response = self.client.get(self.url)
        labels = [tile["label"] for tile in response.context["lead"]["lead_tiles"]]
        self.assertIn("завершён", labels)
        self.assertNotIn("до дедлайна", labels)


class LeadInternActionsTests(TestCase):
    """Тимлид у своего стажёра: заморозка стажировки и отметка в резерв."""

    def setUp(self):
        from apps.training.models import Specialization

        backend = Specialization.objects.create(name="Backend")
        frontend = Specialization.objects.create(name="Frontend")
        self.lead_user = Model.objects.create_user(
            username="+996700000070", password="x", role=User.Role.TEAM_LEAD,
        )
        self.lead = Intern.objects.create(
            full_name="Действий Тимлид", user=self.lead_user, specialization=backend,
        )
        self.project = Project.objects.create(name="Омур")
        self.foreign = Project.objects.create(name="Чужой")
        TeamMember.objects.create(
            project=self.project, intern=self.lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        self.intern = Intern.objects.create(
            full_name="Мой Стажёр", specialization=backend, status="active",
        )
        TeamMember.objects.create(
            project=self.project, intern=self.intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        self.other_direction = Intern.objects.create(
            full_name="Чужое Направление", specialization=frontend, status="active",
        )
        TeamMember.objects.create(
            project=self.project, intern=self.other_direction, role=TeamRole.FRONTEND,
            status=TeamMember.Status.ACTIVE,
        )
        self.client.force_login(self.lead_user)

    def _url(self, name, intern=None, project=None):
        return reverse(name, args=[(project or self.project).pk, (intern or self.intern).pk])

    def test_detail_opens_with_actions(self):
        response = self.client.get(self._url("lead_portal:intern_detail"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Мой Стажёр")
        self.assertContains(response, self._url("lead_portal:intern_to_reserve"))
        self.assertNotContains(response, "Заморозить")  # заморозками управляет руководитель

    def test_lead_has_no_freeze_actions(self):
        """UI-03C: заморозку и возвращение утверждает только руководитель."""
        from django.urls import NoReverseMatch

        for name in ("lead_portal:intern_pause", "lead_portal:intern_unpause"):
            with self.assertRaises(NoReverseMatch):
                self._url(name)

    def test_mark_to_reserve(self):
        from apps.reserve.models import ReserveCandidate

        self.client.post(self._url("lead_portal:intern_to_reserve"), {
            "comment": "Тянет задачи сам, не боится ревью.",
        })
        card = ReserveCandidate.objects.get(intern=self.intern)
        self.assertEqual(card.full_name, "Мой Стажёр")
        self.assertEqual(card.comment_lead, "Тянет задачи сам, не боится ревью.")
        # стажёр, а не сотрудник — попадёт в таблицу стажёров
        self.assertEqual(card.pool, "intern")
        # повторное нажатие не плодит вторую карточку
        self.client.post(self._url("lead_portal:intern_to_reserve"), {"comment": "Ещё"})
        self.assertEqual(ReserveCandidate.objects.filter(intern=self.intern).count(), 1)
        page = self.client.get(self._url("lead_portal:intern_detail"))
        self.assertContains(page, "В резерве кадров")

    def test_to_reserve_offers_comment_first(self):
        response = self.client.get(self._url("lead_portal:intern_to_reserve"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Комментарий тимлида")
        self.assertNotContains(response, "Навыки и технологии")

    def test_comment_is_optional(self):
        from apps.reserve.models import ReserveCandidate

        self.client.post(self._url("lead_portal:intern_to_reserve"), {"comment": ""})
        card = ReserveCandidate.objects.get(intern=self.intern)
        self.assertEqual(card.comment_lead, "")
        self.assertEqual(card.skills, "")

    def test_cannot_touch_other_direction(self):
        from apps.reserve.models import ReserveCandidate

        response = self.client.post(
            self._url("lead_portal:intern_to_reserve", intern=self.other_direction), {"comment": "x"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ReserveCandidate.objects.filter(intern=self.other_direction).exists())

    def test_cannot_touch_foreign_project(self):
        from apps.reserve.models import ReserveCandidate

        response = self.client.post(
            self._url("lead_portal:intern_to_reserve", project=self.foreign), {"comment": "x"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ReserveCandidate.objects.filter(intern=self.intern).exists())

    def test_get_does_not_change_anything(self):
        from apps.reserve.models import ReserveCandidate

        self.client.get(self._url("lead_portal:intern_to_reserve"))
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.status, "active")
        self.assertFalse(ReserveCandidate.objects.filter(intern=self.intern).exists())


class LeadFormerInternTests(TestCase):
    """Карточка того, кто уже вышел из команды: смотреть можно, отметить
    в резерв тоже — замораживать у вышедшего нечего."""

    def setUp(self):
        from apps.training.models import Specialization

        backend = Specialization.objects.create(name="Backend")
        self.lead_user = Model.objects.create_user(
            username="+996700000071", password="x", role=User.Role.TEAM_LEAD,
        )
        self.lead = Intern.objects.create(
            full_name="Вышедших Тимлид", user=self.lead_user, specialization=backend,
        )
        self.project = Project.objects.create(name="Омур")
        self.foreign = Project.objects.create(name="Чужой")
        TeamMember.objects.create(
            project=self.project, intern=self.lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        self.intern = Intern.objects.create(
            full_name="Мой Стажёр", specialization=backend, status="active",
        )
        TeamMember.objects.create(
            project=self.project, intern=self.intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.LEFT,
        )
        self.client.force_login(self.lead_user)

    def _url(self, name, intern=None, project=None):
        return reverse(name, args=[(project or self.project).pk, (intern or self.intern).pk])

    def test_card_opens_for_former_member(self):
        response = self.client.get(self._url("lead_portal:intern_detail"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Мой Стажёр")
        self.assertContains(response, "Вышел(а) из команды")
        self.assertNotContains(response, "Заморозить стажировку")

    def test_team_tab_links_former_member(self):
        response = self.client.get(
            reverse("lead_portal:project_detail", args=[self.project.pk]) + "?tab=team&left=1",
        )
        self.assertContains(response, self._url("lead_portal:intern_detail"))

    def test_former_member_can_be_marked_to_reserve(self):
        from apps.reserve.models import ReserveCandidate

        self.client.post(
            self._url("lead_portal:intern_to_reserve"), {"comment": "Вышел, но толковый"},
        )
        card = ReserveCandidate.objects.get(intern=self.intern)
        self.assertEqual(card.comment_lead, "Вышел, но толковый")

    def test_foreign_project_still_closed(self):
        response = self.client.get(
            self._url("lead_portal:intern_detail", project=self.foreign),
        )
        self.assertEqual(response.status_code, 404)


class LeadFinishedProjectsTests(TestCase):
    """Сданный проект распускает команду — но у тимлида он остаётся
    в портале, иначе вкладка «Завершён» всегда пустая."""

    def setUp(self):
        from apps.projects.models import ProjectStatus
        from apps.training.models import Specialization

        backend = Specialization.objects.create(name="Backend")
        self.lead_user = Model.objects.create_user(
            username="+996700000080", password="x", role=User.Role.TEAM_LEAD,
        )
        self.lead = Intern.objects.create(
            full_name="Сдавший Тимлид", user=self.lead_user, specialization=backend,
        )
        self.done = Project.objects.create(
            name="Сданный", status=ProjectStatus.COMPLETED,
        )
        self.running = Project.objects.create(name="В работе")
        self.taken_away = Project.objects.create(name="Забрали")
        TeamMember.objects.create(
            project=self.done, intern=self.lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.LEFT,
        )
        TeamMember.objects.create(
            project=self.running, intern=self.lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        TeamMember.objects.create(
            project=self.taken_away, intern=self.lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.LEFT,
        )
        self.client.force_login(self.lead_user)

    def test_dashboard_lists_finished_project(self):
        response = self.client.get(reverse("lead_portal:dashboard"))
        names = [p.name for p in response.context["projects"]]
        self.assertIn("Сданный", names)
        self.assertIn("В работе", names)

    def test_completed_tab_is_not_empty(self):
        response = self.client.get(
            reverse("lead_portal:dashboard") + "?status=completed",
        )
        self.assertEqual([p.name for p in response.context["projects"]], ["Сданный"])

    def test_finished_project_card_opens(self):
        response = self.client.get(
            reverse("lead_portal:project_detail", args=[self.done.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Сданный")

    def test_project_taken_away_stays_closed(self):
        """Сняли с активного проекта — доступ теряется, как и раньше."""
        response = self.client.get(reverse("lead_portal:dashboard"))
        names = [p.name for p in response.context["projects"]]
        self.assertNotIn("Забрали", names)
        self.assertEqual(
            self.client.get(
                reverse("lead_portal:project_detail", args=[self.taken_away.pk]),
            ).status_code,
            404,
        )


class LeadMeetingDirectionTests(TestCase):
    """Собрание тимлида — только для его направления."""

    def setUp(self):
        from apps.attendance.models import GroupMeeting, MeetingKind
        from apps.flows.models import Flow, Group
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole
        from apps.training.models import Specialization

        self.design = Specialization.objects.create(name='UX/UI')
        self.backend = Specialization.objects.create(name='Backend')
        self.project = Project.objects.create(name='Омур')
        flow = Flow.objects.create(number=1)
        self.group = Group.objects.create(flow=flow, number=1, project=self.project)

        def person(name, spec, role, lead=False):
            user = Model.objects.create_user(
                username=f'u{name}', password='x',
                role=User.Role.TEAM_LEAD if lead else User.Role.PROJECT_MANAGER,
            )
            intern = Intern.objects.create(full_name=name, specialization=spec, user=user)
            TeamMember.objects.create(
                project=self.project, group=self.group, intern=intern, role=role,
                status=TeamMember.Status.ACTIVE,
            )
            return user, intern

        self.design_lead, self.design_lead_intern = person(
            'Тимлид Дизайна', self.design, TeamRole.TEAM_LEAD, lead=True,
        )
        self.backend_lead, _ = person(
            'Тимлид Бэка', self.backend, TeamRole.TEAM_LEAD, lead=True,
        )
        person('Дизайнер', self.design, TeamRole.UXUI)
        person('Бэкендер', self.backend, TeamRole.BACKEND)
        self.MeetingKind = MeetingKind
        self.GroupMeeting = GroupMeeting

    def _create_meeting(self, user, date='2026-10-09'):
        self.client.force_login(user)
        return self.client.post(
            reverse('lead_portal:meeting_create', args=[self.project.pk]),
            {'date': date},
        )

    def test_meeting_carries_the_lead_direction(self):
        self._create_meeting(self.design_lead)
        meeting = self.GroupMeeting.objects.get()
        self.assertEqual(meeting.direction, 'uxui')
        self.assertEqual(meeting.direction_label, 'UX/UI')

    def test_other_lead_does_not_see_a_foreign_meeting(self):
        self._create_meeting(self.design_lead)
        self.client.force_login(self.backend_lead)
        response = self.client.get(
            reverse('lead_portal:project_detail', args=[self.project.pk]),
            {'tab': 'attendance'},
        )
        self.assertEqual(list(response.context['meetings']), [])

    def test_foreign_meeting_does_not_block_creating_your_own(self):
        self._create_meeting(self.design_lead)
        self._create_meeting(self.backend_lead)
        self.assertEqual(self.GroupMeeting.objects.count(), 2)
        self.assertEqual(
            sorted(self.GroupMeeting.objects.values_list('direction', flat=True)),
            ['backend', 'uxui'],
        )

    def test_foreign_meeting_cannot_be_opened(self):
        self._create_meeting(self.design_lead)
        meeting = self.GroupMeeting.objects.get()
        self.client.force_login(self.backend_lead)
        response = self.client.get(
            reverse('lead_portal:meeting_detail', args=[self.project.pk, meeting.pk]),
        )
        self.assertEqual(response.status_code, 404)

    def test_own_meeting_lists_only_your_direction(self):
        self._create_meeting(self.design_lead)
        meeting = self.GroupMeeting.objects.get()
        self.client.force_login(self.design_lead)
        response = self.client.get(
            reverse('lead_portal:meeting_detail', args=[self.project.pk, meeting.pk]),
        )
        names = [
            member.intern.full_name
            for section in response.context['sections']
            for member in section['members']
        ]
        self.assertEqual(names, ['Дизайнер'])

    def test_unclosed_foreign_meeting_does_not_nag(self):
        """Бэкенд-тимлида не должно блокировать собрание дизайна."""
        from apps.lead_portal import services as lead_services

        self._create_meeting(self.design_lead)
        self.assertIsNone(
            lead_services.pending_meeting(self.project, self.backend_lead),
        )
        self.assertIsNotNone(
            lead_services.pending_meeting(self.project, self.design_lead),
        )

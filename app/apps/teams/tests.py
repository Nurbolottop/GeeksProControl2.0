from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.interns.models import Intern, InternStatus
from apps.projects.models import Project
from apps.teams import services
from apps.teams.forms import TeamMemberForm
from apps.teams.models import TeamMember, TeamRole

User = get_user_model()


class WorkloadTests(TestCase):
    """Загрузка и предупреждение о перегрузе (ТЗ §11)."""

    def setUp(self):
        self.user = User.objects.create_user(username='dev')
        self.person = Intern.objects.create(full_name='Прототипов Черновбек')
        self.project_a = Project.objects.create(name='A')
        self.project_b = Project.objects.create(name='B')

    def test_person_workload_sums_active_memberships(self):
        TeamMember.objects.create(
            project=self.project_a, intern=self.person,
            role=TeamRole.BACKEND, workload=60,
        )
        TeamMember.objects.create(
            project=self.project_b, intern=self.person,
            role=TeamRole.BACKEND, workload=50,
        )
        self.assertEqual(services.person_workload(intern=self.person), 110)

    def test_left_memberships_not_counted(self):
        TeamMember.objects.create(
            project=self.project_a, intern=self.person,
            role=TeamRole.BACKEND, workload=90,
            status=TeamMember.Status.LEFT,
        )
        self.assertEqual(services.person_workload(intern=self.person), 0)

    def test_workload_bands(self):
        self.assertEqual(services.workload_band(30)[0], 'free')
        self.assertEqual(services.workload_band(70)[0], 'normal')
        self.assertEqual(services.workload_band(95)[0], 'high')
        self.assertEqual(services.workload_band(120)[0], 'overload')

    def test_form_warns_when_person_on_many_projects(self):
        for project in (self.project_a, self.project_b):
            TeamMember.objects.create(
                project=project, intern=self.person, role=TeamRole.BACKEND,
            )
        form = TeamMemberForm({'intern': self.person.pk})
        self.assertTrue(form.is_valid(), form.errors)
        warning = form.overload_warning()
        self.assertIsNotNone(warning)
        self.assertIn('2 проектах', warning)

    def test_form_no_warning_for_first_project(self):
        form = TeamMemberForm({'intern': self.person.pk})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.overload_warning())

    def test_member_requires_person(self):
        form = TeamMemberForm({
            'role': TeamRole.BACKEND, 'workload': 40,
            'status': TeamMember.Status.ACTIVE,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('intern', form.errors)


class ActivateInternMembershipTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Проект")

    def test_clears_graduate_status_and_activates(self):
        from apps.interns.models import GraduateStatus

        intern = Intern.objects.create(
            full_name="Выпускник", graduate_status=GraduateStatus.PENDING,
            status=InternStatus.READY,
        )
        member = TeamMember.objects.create(
            project=self.project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        services.activate_intern_membership(member)
        intern.refresh_from_db()
        self.assertEqual(intern.graduate_status, "")
        self.assertEqual(intern.status, InternStatus.ACTIVE)

    def test_noop_for_left_membership(self):
        from apps.interns.models import GraduateStatus

        intern = Intern.objects.create(
            full_name="Выпускник", graduate_status=GraduateStatus.PENDING,
        )
        member = TeamMember.objects.create(
            project=self.project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.LEFT,
        )
        services.activate_intern_membership(member)
        intern.refresh_from_db()
        self.assertEqual(intern.graduate_status, GraduateStatus.PENDING)

    def test_noop_for_slot_without_intern(self):
        member = TeamMember.objects.create(
            project=self.project, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        services.activate_intern_membership(member)  # не должно упасть

    def test_does_not_unpause_frozen_intern(self):
        """Замороженного стажёра статус не трогает — заморозка не то же
        самое, что «ожидает»/«готов»."""
        intern = Intern.objects.create(full_name="Заморожен", status=InternStatus.PAUSED)
        member = TeamMember.objects.create(
            project=self.project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        services.activate_intern_membership(member)
        intern.refresh_from_db()
        self.assertEqual(intern.status, InternStatus.PAUSED)

    def test_logs_to_audit(self):
        from apps.audit.models import AuditLog
        from apps.interns.models import GraduateStatus

        intern = Intern.objects.create(
            full_name="Выпускник", graduate_status=GraduateStatus.PENDING,
        )
        member = TeamMember.objects.create(
            project=self.project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        services.activate_intern_membership(member)
        self.assertTrue(
            AuditLog.objects.filter(
                object_type="Intern", object_id=str(intern.pk),
                action="Статус выпускника снят",
            ).exists(),
        )


class LeaveTeamGraduatesOnProjectEndedTests(TestCase):
    """Снятие с проекта с причиной «Проект завершён» должно превращать
    человека в выпускника — так же, как apps.projects.services.release_team
    делает это при смене статуса всего проекта разом. На практике ПМ/
    тимлиды часто снимают людей по одному, не меняя статус проекта."""

    def setUp(self):
        self.project = Project.objects.create(name="Эпсилонбек")

    def test_marks_graduate_status_pending(self):
        intern = Intern.objects.create(full_name="Стажёр", status=InternStatus.ACTIVE)
        member = TeamMember.objects.create(
            project=self.project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        services.leave_team(member, reason=TeamMember.LeftReason.PROJECT_ENDED)

        from apps.interns.models import GraduateStatus

        intern.refresh_from_db()
        self.assertEqual(intern.graduate_status, GraduateStatus.PENDING)
        self.assertEqual(intern.status, InternStatus.READY)

    def test_other_reasons_do_not_graduate(self):
        intern = Intern.objects.create(full_name="Стажёр")
        member = TeamMember.objects.create(
            project=self.project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        services.leave_team(member, reason=TeamMember.LeftReason.NOT_FIT)
        intern.refresh_from_db()
        self.assertEqual(intern.graduate_status, "")

    def test_does_not_override_existing_graduate_status(self):
        from apps.interns.models import GraduateStatus

        intern = Intern.objects.create(
            full_name="Уже отказался", graduate_status=GraduateStatus.DECLINED,
        )
        member = TeamMember.objects.create(
            project=self.project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        services.leave_team(member, reason=TeamMember.LeftReason.PROJECT_ENDED)
        intern.refresh_from_db()
        self.assertEqual(intern.graduate_status, GraduateStatus.DECLINED)

    def test_team_lead_not_graduated(self):
        """Тимлид — сотрудник: снятие с проекта не превращает его в
        выпускника, даже с причиной «Проект завершён»."""
        lead = Intern.objects.create(full_name="Тимлид", status=InternStatus.ACTIVE)
        member = TeamMember.objects.create(
            project=self.project, intern=lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        services.leave_team(member, reason=TeamMember.LeftReason.PROJECT_ENDED)
        lead.refresh_from_db()
        self.assertEqual(lead.graduate_status, "")
        self.assertEqual(lead.status, InternStatus.ACTIVE)

    def test_paused_intern_status_untouched(self):
        intern = Intern.objects.create(full_name="Заморожен", status=InternStatus.PAUSED)
        member = TeamMember.objects.create(
            project=self.project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        services.leave_team(member, reason=TeamMember.LeftReason.PROJECT_ENDED)
        intern.refresh_from_db()
        self.assertEqual(intern.status, InternStatus.PAUSED)


class AddNewPersonTests(TestCase):
    """Человека, которого нет в базе, можно завести прямо из формы команды."""

    def setUp(self):
        from apps.projects.services import create_project
        from apps.training.models import Specialization

        self.user = User.objects.create_user(username="pm", password="x")
        self.client.force_login(self.user)
        self.project = create_project(Project(name="Омур", recruitment_open=True))
        self.spec = Specialization.objects.create(name="Backend")

    def test_new_person_created_and_added_to_team(self):
        response = self.client.post(
            reverse("teams:member_add", args=[self.project.pk]),
            {"new_person": "Демобаев Тетабек", "new_spec": self.spec.pk,
             "workload": 50},
        )
        self.assertEqual(response.status_code, 302)
        intern = Intern.objects.get(full_name="Демобаев Тетабек")
        self.assertEqual(intern.specialization, self.spec)
        self.assertTrue(
            TeamMember.objects.filter(project=self.project, intern=intern).exists(),
        )

    def test_existing_person_not_duplicated(self):
        Intern.objects.create(full_name="Демобаев Тетабек")
        self.client.post(
            reverse("teams:member_add", args=[self.project.pk]),
            {"new_person": "Демобаев Тетабек", "workload": 50},
        )
        self.assertEqual(Intern.objects.filter(full_name="Демобаев Тетабек").count(), 1)

    def test_form_page_lists_specializations(self):
        response = self.client.get(
            reverse("teams:member_add", args=[self.project.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Backend")
        self.assertContains(response, "new_person")


class MemberAddActivatesGraduateTests(TestCase):
    """Добавление уже существующего стажёра-выпускника в команду должно
    снимать ему статус выпускника и активировать — как и при добавлении
    новой карточки (см. AddNewPersonTests), а не только когда это делает
    ПМ через карточку самого стажёра (interns:project_add)."""

    def setUp(self):
        from apps.interns.models import GraduateStatus
        from apps.projects.services import create_project
        from apps.training.models import Specialization

        self.GraduateStatus = GraduateStatus
        self.backend_spec = Specialization.objects.create(name="Backend")
        self.user = User.objects.create_user(username="head3", password="x")
        self.client.force_login(self.user)
        self.project = create_project(Project(name="Вистайл", recruitment_open=True))

    def test_member_add_clears_graduate_status(self):
        graduate = Intern.objects.create(
            full_name="Выпускник Общий",
            graduate_status=self.GraduateStatus.PENDING, status=InternStatus.READY,
        )
        self.client.post(
            reverse("teams:member_add", args=[self.project.pk]),
            {"intern": graduate.pk, "workload": 50},
        )
        graduate.refresh_from_db()
        self.assertEqual(graduate.graduate_status, "")
        self.assertEqual(graduate.status, InternStatus.ACTIVE)

    def test_member_edit_reassignment_clears_graduate_status(self):
        graduate = Intern.objects.create(
            full_name="Выпускник На Замену", specialization=self.backend_spec,
            graduate_status=self.GraduateStatus.PENDING, status=InternStatus.READY,
        )
        member = TeamMember.objects.create(
            project=self.project, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        self.client.post(
            reverse("teams:member_edit", args=[member.pk]),
            {"intern": graduate.pk, "status": "active"},
        )
        graduate.refresh_from_db()
        self.assertEqual(graduate.graduate_status, "")
        self.assertEqual(graduate.status, InternStatus.ACTIVE)


class RoleSectionAddTests(TestCase):
    """В каждое направление добавляют отдельно, список фильтруется."""

    def setUp(self):
        from apps.projects.services import create_project
        from apps.training.models import Specialization

        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.project = create_project(Project(name="Балажан"))
        self.pm_spec = Specialization.objects.create(name="PM")
        self.back_spec = Specialization.objects.create(name="Backend")
        self.pm = Intern.objects.create(full_name="Лямбдагуль", specialization=self.pm_spec)
        self.dev = Intern.objects.create(full_name="Вымыслов Сигмабек", specialization=self.back_spec)

    def _url(self, role):
        base = reverse("teams:member_add", args=[self.project.pk])
        return f"{base}?role={role}"

    def test_pm_section_lists_only_pms(self):
        response = self.client.get(self._url("pm"))
        self.assertEqual(response.status_code, 200)
        people = [p["name"] for p in response.context["people"]]
        self.assertIn("Лямбдагуль", people)
        self.assertNotIn("Вымыслов Сигмабек", people)

    def test_backend_section_lists_only_backend(self):
        response = self.client.get(self._url("backend"))
        people = [p["name"] for p in response.context["people"]]
        self.assertEqual(people, ["Вымыслов Сигмабек"])

    def test_role_applied_on_save(self):
        self.client.post(self._url("pm"), {"intern": self.pm.pk, "role": "pm"})
        member = TeamMember.objects.get(project=self.project, intern=self.pm)
        self.assertEqual(member.role, TeamRole.PROJECT_MANAGER)

    def test_team_lead_section_lists_only_existing_leads(self):
        """Тут добавляют существующего тимлида ещё на один проект — не
        повышают случайного стажёра. Первого тимлида направления заводят
        через страницу «Тимлиды» (там есть «+ новый человек»)."""
        from apps.projects.services import create_project

        other_project = create_project(Project(name="Эпсилонбек"))
        TeamMember.objects.create(
            project=other_project, intern=self.dev, role=TeamRole.TEAM_LEAD,
        )
        response = self.client.get(self._url("team_lead"))
        people = [p["name"] for p in response.context["people"]]
        self.assertIn("Вымыслов Сигмабек", people)
        self.assertNotIn("Лямбдагуль", people)

    def test_team_lead_section_rejects_intern_who_is_not_a_lead(self):
        response = self.client.post(
            self._url("team_lead"),
            {"intern": self.dev.pk, "role": "team_lead"},
        )
        self.assertFalse(
            TeamMember.objects.filter(project=self.project, intern=self.dev).exists(),
        )

    def test_team_lead_section_accepts_existing_lead(self):
        from apps.projects.services import create_project

        other_project = create_project(Project(name="Эпсилонбек"))
        TeamMember.objects.create(
            project=other_project, intern=self.dev, role=TeamRole.TEAM_LEAD,
        )
        self.client.post(
            self._url("team_lead"),
            {"intern": self.dev.pk, "role": "team_lead"},
        )
        member = TeamMember.objects.get(project=self.project, intern=self.dev)
        self.assertEqual(member.role, TeamRole.TEAM_LEAD)

    def test_empty_sections_shown_on_team_tab(self):
        response = self.client.get(f"{self.project.get_absolute_url()}?tab=team")
        roles = [s["role"] for s in response.context["team_sections"]]
        for role in ("pm", "team_lead", "uxui", "backend", "frontend", "qa"):
            self.assertIn(role, roles)

    def test_mobile_project_shows_mobile_section_instead_of_empty_frontend(self):
        """Тип проекта Mobile App — вместо пустого Frontend всегда Mobile
        (как lifecycle_stages() меняет этап Frontend на «Мобильная
        разработка»)."""
        from apps.projects.models import ProjectType

        self.project.project_type = ProjectType.objects.create(
            name="Mobile App", is_mobile=True,
        )
        self.project.save(update_fields=["project_type"])

        response = self.client.get(f"{self.project.get_absolute_url()}?tab=team")
        roles = [s["role"] for s in response.context["team_sections"]]
        self.assertIn("mobile", roles)
        self.assertNotIn("frontend", roles)


class PausedInternBadgeTests(TestCase):
    """Замороженный стажёр остаётся активным участником команды (не
    «вышел»), но на странице команды это должно быть видно — иначе
    выглядит как будто он просто активно работает."""

    def setUp(self):
        from apps.projects.services import create_project

        self.user = User.objects.create_user(username="head_pause_badge", password="x")
        self.client.force_login(self.user)
        self.project = create_project(Project(name="Гринбонус"))
        self.intern = Intern.objects.create(
            full_name="Шаблоналиева Примерана", status=InternStatus.PAUSED,
        )
        TeamMember.objects.create(
            project=self.project, intern=self.intern, role=TeamRole.UXUI,
            status=TeamMember.Status.ACTIVE,
        )

    def test_paused_badge_shown_on_project_team_tab(self):
        response = self.client.get(f"{self.project.get_absolute_url()}?tab=team")
        self.assertContains(response, "Приостановлен")

    def test_no_paused_badge_for_active_intern(self):
        self.intern.status = InternStatus.ACTIVE
        self.intern.save(update_fields=["status"])
        response = self.client.get(f"{self.project.get_absolute_url()}?tab=team")
        self.assertNotContains(response, "Приостановлен")


class LeadCurrentProjectsTests(TestCase):
    """В карточке тимлида — только то, что он ведёт сейчас."""

    def setUp(self):
        from apps.projects.models import ProjectStatus
        from apps.projects.services import create_project
        from apps.training.models import Specialization

        self.user = User.objects.create_user(username="head9", password="x")
        self.client.force_login(self.user)
        self.spec = Specialization.objects.create(name="Backend")
        self.person = Intern.objects.create(full_name="Ведущий Тимлид", specialization=self.spec)
        self.active = create_project(Project(name="Идёт"))
        self.done = create_project(Project(name="Завершён"))
        self.done.status = ProjectStatus.COMPLETED
        self.done.save(update_fields=["status"])
        self.paused = create_project(Project(name="Приостановлен"))
        self.paused.status = ProjectStatus.PAUSED
        self.paused.save(update_fields=["status"])
        for project in (self.active, self.done, self.paused):
            TeamMember.objects.create(
                project=project, intern=self.person, role=TeamRole.TEAM_LEAD,
            )

    def _row(self):
        response = self.client.get(reverse("teams:lead_list"))
        person = response.context["page"].object_list[0]
        return response, {"projects": person.ui_active, "past": person.ui_past}

    def test_only_active_projects_in_the_card(self):
        _, row = self._row()
        self.assertEqual([m.project.name for m in row["projects"]], ["Идёт"])

    def test_finished_and_paused_go_to_past(self):
        _, row = self._row()
        self.assertEqual(
            sorted(m.project.name for m in row["past"]), ["Завершён", "Приостановлен"],
        )

    def test_past_projects_are_behind_a_toggle(self):
        response, _ = self._row()
        self.assertContains(response, "Прошлые проекты: 2")

    def test_left_membership_counts_as_past(self):
        member = TeamMember.objects.get(project=self.active, intern=self.person)
        member.status = TeamMember.Status.LEFT
        member.save(update_fields=["status"])
        _, row = self._row()
        self.assertEqual(row["projects"], [])
        self.assertEqual(len(row["past"]), 3)

    def test_second_active_project_is_listed(self):
        TeamMember.objects.create(
            project=Project.objects.create(name="Второй"), intern=self.person,
            role=TeamRole.TEAM_LEAD,
        )
        _, row = self._row()
        self.assertEqual(sorted(m.project.name for m in row["projects"]), ["Второй", "Идёт"])

    def test_lead_without_active_projects_is_still_listed(self):
        TeamMember.objects.filter(project=self.active).delete()
        response, row = self._row()
        self.assertEqual(row["projects"], [])
        self.assertContains(response, "Ведущий Тимлид")
        self.assertContains(response, "не ведёт ни одного активного проекта")


class LeadSectionTests(TestCase):
    """Раздел «Тимлиды»: назначить, снять, посмотреть по проектам."""

    def setUp(self):
        from apps.projects.services import create_project
        from apps.training.models import Specialization

        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.project = create_project(Project(name="Балажан"))
        self.spec = Specialization.objects.create(name="Backend")
        self.person = Intern.objects.create(
            full_name="Ракетбеков Дельтабек", specialization=self.spec,
        )

    def test_page_lists_leads(self):
        TeamMember.objects.create(
            project=self.project, intern=self.person, role=TeamRole.TEAM_LEAD,
        )
        response = self.client.get(reverse("teams:lead_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ракетбеков Дельтабек")
        self.assertEqual(response.context["total"], 1)

    def test_assign_lead(self):
        """Этап 6: стажёра назначают тимлидом только с явным повышением."""
        self.client.post(reverse("teams:lead_add"), {
            "intern": self.person.pk, "project": self.project.pk, "promote": "1",
        })
        member = TeamMember.objects.get(project=self.project, intern=self.person, status="active")
        self.assertEqual(member.role, TeamRole.TEAM_LEAD)

    def test_existing_member_becomes_lead(self):
        member = TeamMember.objects.create(
            project=self.project, intern=self.person, role=TeamRole.BACKEND,
        )
        self.client.post(reverse("teams:lead_add"), {
            "intern": self.person.pk, "project": self.project.pk, "promote": "1",
        })
        member.refresh_from_db()
        # участие стажёра закрыто (история цела), назначение тимлида — отдельное
        self.assertEqual((member.status, member.left_reason), ("left", TeamMember.LeftReason.PROMOTED))
        self.assertTrue(TeamMember.objects.filter(project=self.project, intern=self.person,
                                                  role=TeamRole.TEAM_LEAD, status="active").exists())

    def test_remove_lead_keeps_person(self):
        member = TeamMember.objects.create(
            project=self.project, intern=self.person, role=TeamRole.TEAM_LEAD,
        )
        self.client.post(reverse("teams:lead_remove", args=[member.pk]))
        member.refresh_from_db()
        self.assertEqual(member.status, TeamMember.Status.LEFT)
        self.assertTrue(Intern.objects.filter(pk=self.person.pk).exists())

    def test_intern_without_promotion_is_not_assigned_lead(self):
        self.client.post(reverse("teams:lead_add"), {"intern": self.person.pk, "project": self.project.pk})
        self.assertFalse(TeamMember.objects.filter(intern=self.person, role=TeamRole.TEAM_LEAD).exists())

    def test_assign_lead_returns_to_given_next_url(self):
        """Со страницы стажёра — назад на страницу стажёра, а не в общий
        список тимлидов, чтобы не терять место."""
        response = self.client.post(reverse("teams:lead_add"), {
            "intern": self.person.pk, "project": self.project.pk, "promote": "1",
            "next": self.person.get_absolute_url(),
        })
        self.assertRedirects(response, self.person.get_absolute_url())

    def test_assign_lead_ignores_unsafe_next_url(self):
        response = self.client.post(reverse("teams:lead_add"), {
            "intern": self.person.pk, "project": self.project.pk,
            "next": "https://evil.example.com/",
        })
        self.assertRedirects(response, reverse("teams:lead_list"))


class InternDetailLeadPromotionTests(TestCase):
    """Со страницы человека его повышают до тимлида. Должность общая:
    тимлидом он становится на всех своих проектах сразу."""

    def setUp(self):
        from apps.projects.services import create_project
        from apps.training.models import Specialization

        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.project = create_project(Project(name="Балажан"))
        self.spec = Specialization.objects.create(name="UX/UI")
        self.person = Intern.objects.create(
            full_name="Сценбаева Пилотгуль", specialization=self.spec,
        )

    def test_button_shown_for_regular_member(self):
        TeamMember.objects.create(
            project=self.project, intern=self.person, role=TeamRole.UXUI,
            status=TeamMember.Status.ACTIVE,
        )
        response = self.client.get(self.person.get_absolute_url())
        self.assertContains(response, "Повысить до тимлида")

    def test_button_hidden_when_already_lead(self):
        self.person.position = "lead"
        self.person.save(update_fields=["position"])
        response = self.client.get(self.person.get_absolute_url())
        self.assertNotContains(response, "Повысить до тимлида")
        self.assertContains(response, "Снять с тимлида")

    def test_assigning_lead_on_project_also_raises_position(self):
        """Этап 6: назначение стажёра тимлидом — явное повышение: стажировка
        завершается, участия стажёра закрываются, тимлид — только там, куда назначили."""
        second = Project.objects.create(name="Палароид")
        member = TeamMember.objects.create(
            project=self.project, intern=self.person, role=TeamRole.UXUI,
            status=TeamMember.Status.ACTIVE,
        )
        other = TeamMember.objects.create(
            project=second, intern=self.person, role=TeamRole.UXUI,
            status=TeamMember.Status.ACTIVE,
        )
        response = self.client.post(reverse("teams:lead_add"), {
            "intern": self.person.pk, "project": self.project.pk, "promote": "1",
            "next": self.person.get_absolute_url(),
        })
        self.assertRedirects(response, self.person.get_absolute_url())
        member.refresh_from_db()
        other.refresh_from_db()
        self.person.refresh_from_db()
        self.assertEqual((member.status, other.status), ("left", "left"))
        self.assertEqual(TeamMember.objects.get(intern=self.person, status="active").project, self.project)
        self.assertEqual(self.person.position, "lead")


class PMSectionTests(TestCase):
    """Раздел «ПМ»: отдельный отфильтрованный список — не нужно искать
    руководителей проектов в общем списке стажёров."""

    def setUp(self):
        from apps.projects.services import create_project

        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.project = create_project(Project(name="Балажан"))
        self.person = Intern.objects.create(full_name="Ракетбекова Зетайым")

    def test_page_lists_pms(self):
        TeamMember.objects.create(
            project=self.project, intern=self.person, role=TeamRole.PROJECT_MANAGER,
        )
        response = self.client.get(reverse("teams:pm_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ракетбекова Зетайым")
        self.assertEqual(response.context["total"], 1)

    def test_assign_pm(self):
        self.client.post(reverse("teams:pm_add"), {
            "intern": self.person.pk, "project": self.project.pk,
        })
        member = TeamMember.objects.get(project=self.project, intern=self.person)
        self.assertEqual(member.role, TeamRole.PROJECT_MANAGER)

    def test_existing_member_becomes_pm(self):
        member = TeamMember.objects.create(
            project=self.project, intern=self.person, role=TeamRole.BACKEND,
        )
        self.client.post(reverse("teams:pm_add"), {
            "intern": self.person.pk, "project": self.project.pk,
        })
        member.refresh_from_db()
        self.assertEqual(member.role, TeamRole.PROJECT_MANAGER)

    def test_remove_pm_keeps_person(self):
        member = TeamMember.objects.create(
            project=self.project, intern=self.person, role=TeamRole.PROJECT_MANAGER,
        )
        self.client.post(reverse("teams:pm_remove", args=[member.pk]))
        member.refresh_from_db()
        self.assertEqual(member.status, TeamMember.Status.LEFT)
        self.assertTrue(Intern.objects.filter(pk=self.person.pk).exists())

    def test_pm_not_shown_on_lead_page(self):
        TeamMember.objects.create(
            project=self.project, intern=self.person, role=TeamRole.PROJECT_MANAGER,
        )
        response = self.client.get(reverse("teams:lead_list"))
        self.assertNotContains(response, "Ракетбекова Зетайым")


class InternDeleteTests(TestCase):
    """Стажёра можно удалить из базы."""

    def setUp(self):
        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.person = Intern.objects.create(full_name="Тестовый Человек")

    def test_delete_removes_person(self):
        response = self.client.post(
            reverse("interns:delete", args=[self.person.pk]),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Intern.objects.filter(pk=self.person.pk).exists())

    def test_get_does_not_delete(self):
        self.client.get(reverse("interns:delete", args=[self.person.pk]))
        self.assertTrue(Intern.objects.filter(pk=self.person.pk).exists())

    def test_delete_removes_team_memberships(self):
        from apps.projects.services import create_project

        project = create_project(Project(name="Омур", recruitment_open=True))
        TeamMember.objects.create(
            project=project, intern=self.person, role=TeamRole.BACKEND,
        )
        self.client.post(reverse("interns:delete", args=[self.person.pk]))
        self.assertEqual(project.team_members.count(), 0)


class MemberDeleteTests(TestCase):
    """Участника можно убрать из команды, команду — расформировать."""

    def setUp(self):
        from apps.projects.services import create_project

        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.project = create_project(Project(name="Биклин"))
        self.person = Intern.objects.create(full_name="Каппагуль")
        self.member = TeamMember.objects.create(
            project=self.project, intern=self.person, role=TeamRole.PROJECT_MANAGER,
        )

    def test_member_removed_person_stays(self):
        response = self.client.post(
            reverse("teams:member_delete", args=[self.member.pk]),
            {"left_reason": "not_fit"},
        )
        self.assertEqual(response.status_code, 302)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, TeamMember.Status.LEFT)
        self.assertEqual(self.member.left_reason, "not_fit")
        self.assertTrue(Intern.objects.filter(pk=self.person.pk).exists())

    def test_get_does_not_remove_member(self):
        self.client.get(reverse("teams:member_delete", args=[self.member.pk]))
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, TeamMember.Status.ACTIVE)

    def test_team_cleared(self):
        other = Intern.objects.create(full_name="Омикронхан")
        TeamMember.objects.create(
            project=self.project, intern=other, role=TeamRole.BACKEND,
        )
        self.client.post(reverse("teams:team_clear", args=[self.project.pk]))
        self.assertEqual(
            self.project.team_members.filter(status=TeamMember.Status.ACTIVE).count(), 0,
        )
        self.assertEqual(self.project.team_members.count(), 2)
        self.assertEqual(Intern.objects.count(), 2)

    def test_duplicate_membership_removed_by_command(self):
        from django.core.management import call_command

        TeamMember.objects.create(
            project=self.project, intern=self.person,
            role=TeamRole.PROJECT_MANAGER,
        )
        self.assertEqual(self.project.team_members.count(), 2)
        call_command("dedupe_members", verbosity=0)
        self.assertEqual(self.project.team_members.count(), 1)


class LeadArchiveTests(TestCase):
    """Тимлид, который у нас больше не работает, уходит в архив, а не удаляется."""

    def setUp(self):
        from apps.accounts.models import User
        from apps.interns.models import Intern, InternEvaluation
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        self.head = User.objects.create_user(username="head-arch", password="x")
        self.client.force_login(self.head)
        self.login = User.objects.create_user(username="lead-login", password="x")
        self.lead = Intern.objects.create(full_name="Вариантбек", user=self.login)
        self.project = Project.objects.create(name="Омур")
        self.old_project = Project.objects.create(name="Старый")
        self.member = TeamMember.objects.create(
            project=self.project, intern=self.lead,
            role=TeamRole.TEAM_LEAD, status=TeamMember.Status.ACTIVE,
        )
        TeamMember.objects.create(
            project=self.old_project, intern=self.lead,
            role=TeamRole.TEAM_LEAD, status=TeamMember.Status.LEFT,
        )
        self.evaluation = InternEvaluation.objects.create(
            intern=self.lead, project=self.project, evaluator=self.head,
            hard_skills=1, quality=1, speed=1, responsibility=1,
            communication=1, teamwork=1, independence=1,
        )

    def _archive(self):
        return self.client.post(reverse("interns:archive", args=[self.lead.pk]))

    def test_archive_keeps_history_and_ends_projects(self):
        from apps.audit.models import AuditLog
        from apps.teams.models import TeamMember

        response = self._archive()
        self.assertRedirects(
            response, self.lead.get_absolute_url(), fetch_redirect_response=False,
        )
        self.lead.refresh_from_db()
        self.member.refresh_from_db()
        self.login.refresh_from_db()
        self.assertTrue(self.lead.is_archived)
        self.assertIsNotNone(self.lead.archived_at)
        self.assertEqual(self.member.status, TeamMember.Status.LEFT)
        self.assertIsNotNone(self.member.left_at)
        # тимлид всегда в резерве кадров — вход остаётся, чтобы вести резюме
        self.assertTrue(self.login.is_active)
        # ничего не удалено
        self.assertEqual(self.lead.team_memberships.count(), 2)
        self.assertTrue(self.lead.evaluations.filter(pk=self.evaluation.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(object_id=str(self.lead.pk), action="В архив").exists()
        )

    def test_archived_lead_leaves_working_list(self):
        self._archive()
        response = self.client.get(reverse("teams:lead_list"))
        self.assertEqual(response.context["total"], 0)
        self.assertEqual(
            [p.pk for p in response.context["archived_leads"]], [self.lead.pk],
        )

    def test_detail_shows_archive_state(self):
        response = self.client.get(self.lead.get_absolute_url())
        self.assertContains(response, reverse("interns:archive", args=[self.lead.pk]))
        self._archive()
        response = self.client.get(self.lead.get_absolute_url())
        self.assertContains(response, "В архиве с")
        self.assertContains(response, "Бывший тимлид")
        self.assertContains(response, reverse("interns:unarchive", args=[self.lead.pk]))

    def test_unarchive_restores_login_but_not_projects(self):
        from apps.teams.models import TeamMember

        self._archive()
        self.client.post(reverse("interns:unarchive", args=[self.lead.pk]))
        self.lead.refresh_from_db()
        self.login.refresh_from_db()
        self.member.refresh_from_db()
        self.assertFalse(self.lead.is_archived)
        self.assertTrue(self.login.is_active)
        self.assertEqual(self.member.status, TeamMember.Status.LEFT)

    def test_get_does_not_archive(self):
        self.client.get(reverse("interns:archive", args=[self.lead.pk]))
        self.lead.refresh_from_db()
        self.assertFalse(self.lead.is_archived)

    def test_archived_cannot_be_assigned_again(self):
        self._archive()
        from apps.projects.models import Project

        new_project = Project.objects.create(name="Новый")
        self.client.post(
            reverse("teams:lead_add"), {"project": new_project.pk, "intern": self.lead.pk},
        )
        self.assertFalse(new_project.team_members.filter(intern=self.lead).exists())
        self.client.post(
            reverse("interns:project_add", args=[self.lead.pk]), {"project": new_project.pk},
        )
        self.assertFalse(new_project.team_members.filter(intern=self.lead).exists())

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.interns import services
from apps.interns.models import (
    GraduateStatus, Intern, InternEvaluation, InternStatus, ProfileFormLink,
    ProfileFormSubmission, ResumeBankStatus, TalentReserveCandidate,
)
from apps.interns.services import add_evaluation


class InternRatingTests(TestCase):
    """Средний рейтинг стажёра (ТЗ §12.1)."""

    def test_average_of_single_evaluation(self):
        intern = Intern.objects.create(full_name='Тест Тестов')
        evaluation = InternEvaluation(
            intern=intern, hard_skills=5, quality=4, speed=4,
            responsibility=5, communication=3, teamwork=4, independence=4,
        )
        add_evaluation(evaluation)
        intern.refresh_from_db()
        self.assertEqual(intern.rating, Decimal('4.14'))

    def test_average_over_multiple_evaluations(self):
        intern = Intern.objects.create(full_name='Тест Тестов')
        add_evaluation(InternEvaluation(
            intern=intern, hard_skills=5, quality=5, speed=5,
            responsibility=5, communication=5, teamwork=5, independence=5,
        ))
        add_evaluation(InternEvaluation(
            intern=intern, hard_skills=3, quality=3, speed=3,
            responsibility=3, communication=3, teamwork=3, independence=3,
        ))
        intern.refresh_from_db()
        self.assertEqual(intern.rating, Decimal('4.00'))


class LeadsHiddenFromInternListTests(TestCase):
    """Тимлиды — сотрудники, в списке стажёров их не показываем."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        self.user = get_user_model().objects.create_user(
            username="head", password="x",
        )
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="Балажан", recruitment_open=True)
        self.lead = Intern.objects.create(full_name="Ракетбеков Дельтабек")
        self.dev = Intern.objects.create(full_name="Вымыслов Сигмабек")
        TeamMember.objects.create(
            project=self.project, intern=self.lead, role=TeamRole.TEAM_LEAD,
        )
        TeamMember.objects.create(
            project=self.project, intern=self.dev, role=TeamRole.BACKEND,
        )

    def test_lead_not_in_list(self):
        response = self.client.get(reverse("interns:list"))
        names = [p.full_name for p in response.context["page"].object_list]
        self.assertIn("Вымыслов Сигмабек", names)
        self.assertNotIn("Ракетбеков Дельтабек", names)

    def test_lead_card_still_opens(self):
        response = self.client.get(self.lead.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ракетбеков Дельтабек")

    def test_lead_badge_says_lead(self):
        response = self.client.get(self.lead.get_absolute_url())
        self.assertEqual(response.context["kind"], "Тимлид")


class PMsStayInInternListTests(TestCase):
    """ПМ — тоже стажёр: он есть и в общем списке, и в цифрах. Сотрудник
    у нас только тимлид."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        self.user = get_user_model().objects.create_user(
            username="head", password="x",
        )
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="Балажан", recruitment_open=True)
        self.pm = Intern.objects.create(full_name="Ракетбекова Зетайым")
        self.dev = Intern.objects.create(full_name="Вымыслов Сигмабек")
        TeamMember.objects.create(
            project=self.project, intern=self.pm, role=TeamRole.PROJECT_MANAGER,
            status=TeamMember.Status.ACTIVE,
        )
        TeamMember.objects.create(
            project=self.project, intern=self.dev, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )

    def test_pm_is_in_list(self):
        response = self.client.get(reverse("interns:list"))
        names = [p.full_name for p in response.context["page"].object_list]
        self.assertIn("Вымыслов Сигмабек", names)
        self.assertIn("Ракетбекова Зетайым", names)

    def test_pm_card_still_opens(self):
        response = self.client.get(self.pm.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ракетбекова Зетайым")


class InternKindBadgeTests(TestCase):
    """ПМ — тоже стажёр, а не отдельная категория (в отличие от тимлида,
    который считается сотрудником): бейдж «Кто в команде» не должен
    превращаться в отдельное «Project Manager», дублируя направление «PM»."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        self.user = get_user_model().objects.create_user(
            username="head", password="x",
        )
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="Балажан", recruitment_open=True)
        self.pm = Intern.objects.create(full_name="Ракетбекова Зетайым")
        TeamMember.objects.create(
            project=self.project, intern=self.pm, role=TeamRole.PROJECT_MANAGER,
            status=TeamMember.Status.ACTIVE,
        )

    def test_pm_badge_is_intern_not_project_manager(self):
        """Проверяем именно верхний бейдж «Кто в команде», а не факт
        упоминания роли — «— Project Manager» у проекта в списке ниже
        законно и должен остаться: там честно показана роль на проекте."""
        response = self.client.get(self.pm.get_absolute_url())
        self.assertEqual(response.context["kind"], "Стажёр")
        # 2.0: верхний бейдж — «Новый стажёр» (не «Project Manager»)
        self.assertContains(response, '<span class="gp-badge gp-badge--info">Новый стажёр</span>')


class InternListProjectColumnTests(TestCase):
    """В общем списке стажёров видно, над каким проектом кто работает."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(
            username="head", password="x",
        )
        self.client.force_login(self.user)

    def test_shows_current_project(self):
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        project = Project.objects.create(name="Балажан", recruitment_open=True)
        intern = Intern.objects.create(full_name="Модельбек Тестов")
        TeamMember.objects.create(
            project=project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        response = self.client.get(reverse("interns:list"))
        self.assertContains(response, "Балажан")

    def test_no_project_shows_dash(self):
        Intern.objects.create(full_name="Без проекта Тестов")
        response = self.client.get(reverse("interns:list"))
        self.assertContains(response, "—")

    def test_left_membership_not_shown_as_current(self):
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        project = Project.objects.create(name="Завершённый проект")
        intern = Intern.objects.create(full_name="Вышел Тестов")
        TeamMember.objects.create(
            project=project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.LEFT,
        )
        response = self.client.get(reverse("interns:list"))
        self.assertNotContains(response, "Завершённый проект")

    def test_project_lookup_does_not_scale_per_intern(self):
        """Больше стажёров с проектами не должно давать N+1 запросов."""
        from django.db import connection, reset_queries
        from django.test import override_settings

        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        project = Project.objects.create(name="Балажан", recruitment_open=True)
        intern = Intern.objects.create(full_name="Модельбек Тестов")
        TeamMember.objects.create(
            project=project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )

        with override_settings(DEBUG=True):
            reset_queries()
            self.client.get(reverse("interns:list"))
            baseline = len(connection.queries)

            for i in range(10):
                extra_project = Project.objects.create(name=f"Проект {i}")
                extra_intern = Intern.objects.create(full_name=f"Стажёр {i}")
                TeamMember.objects.create(
                    project=extra_project, intern=extra_intern,
                    role=TeamRole.BACKEND, status=TeamMember.Status.ACTIVE,
                )

            reset_queries()
            self.client.get(reverse("interns:list"))
            with_more_interns = len(connection.queries)

        self.assertLess(with_more_interns, baseline + 10)


class InternWaitingFilterTests(TestCase):
    """Быстрый способ найти новеньких, ждущих распределения на проект."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(
            username="head", password="x",
        )
        self.client.force_login(self.user)

    def test_section_shows_waiting_count(self):
        Intern.objects.create(full_name="Новенький", status=InternStatus.WAITING)
        Intern.objects.create(full_name="Активный", status=InternStatus.ACTIVE)
        response = self.client.get(reverse("interns:list"))
        self.assertEqual(response.context["waiting_count"], 1)
        self.assertContains(response, "Новенькие")
        self.assertContains(response, "ждут, когда их добавят на проект")

    def test_no_section_when_nobody_waiting(self):
        Intern.objects.create(full_name="Активный", status=InternStatus.ACTIVE)
        response = self.client.get(reverse("interns:list"))
        self.assertNotContains(response, "Новенькие")

    def test_status_filter_shows_only_waiting(self):
        Intern.objects.create(full_name="Новенький", status=InternStatus.WAITING)
        Intern.objects.create(full_name="Активный", status=InternStatus.ACTIVE)
        response = self.client.get(reverse("interns:list"), {"status": "waiting"})
        names = [i.full_name for i in response.context["page"].object_list]
        self.assertEqual(names, ["Новенький"])
        self.assertContains(response, "Показать всех")

    def test_waiting_but_already_on_project_is_excluded(self):
        """Если статус почему-то не успел обновиться (или его сбросили
        вручную), но проект уже есть — это не «новенький», не в счёт."""
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        project = Project.objects.create(name="Балажан", recruitment_open=True)
        intern = Intern.objects.create(
            full_name="Уже на проекте", status=InternStatus.WAITING,
        )
        TeamMember.objects.create(
            project=project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        response = self.client.get(reverse("interns:list"))
        self.assertEqual(response.context["waiting_count"], 0)
        response = self.client.get(reverse("interns:list"), {"status": "waiting"})
        self.assertNotContains(response, "Уже на проекте")

    def test_waiting_count_excludes_leads(self):
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        project = Project.objects.create(name="Балажан", recruitment_open=True)
        lead = Intern.objects.create(
            full_name="Тимлид Новенький", status=InternStatus.WAITING,
        )
        TeamMember.objects.create(
            project=project, intern=lead, role=TeamRole.TEAM_LEAD,
        )
        response = self.client.get(reverse("interns:list"))
        self.assertEqual(response.context["waiting_count"], 0)


class InternProjectAddRemoveTests(TestCase):
    """С карточки стажёра можно назначить/снять проект напрямую."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.training.models import Specialization

        self.user = get_user_model().objects.create_user(
            username="head", password="x",
        )
        self.client.force_login(self.user)
        self.spec = Specialization.objects.create(name="Backend")
        self.intern = Intern.objects.create(
            full_name="Модельбек Тестов", specialization=self.spec,
        )

    def test_add_to_project_creates_membership_with_role_from_specialization(self):
        from apps.projects.models import Project
        from apps.teams.models import TeamMember

        project = Project.objects.create(name="Балажан", recruitment_open=True)
        response = self.client.post(
            reverse("interns:project_add", args=[self.intern.pk]),
            {"project": project.pk, "comment": ""},
        )
        member = TeamMember.objects.get(intern=self.intern, project=project)
        self.assertEqual(member.role, "backend")
        self.assertEqual(member.status, TeamMember.Status.ACTIVE)
        self.assertRedirects(response, self.intern.get_absolute_url())

    def test_detail_page_has_no_manual_add(self):
        # 11F: в проект — только через Telegram-бот, ручного «Добавить в проект» в карточке нет
        response = self.client.get(self.intern.get_absolute_url())
        self.assertNotContains(
            response, reverse("interns:project_add", args=[self.intern.pk]),
        )

    def test_add_to_project_moves_waiting_status_to_active(self):
        """Раньше проект назначался, а статус «Ожидает стажировки» так и
        оставался висеть — человек по факту на проекте, но по статусу
        числится ещё не начавшим."""
        from apps.projects.models import Project

        self.assertEqual(self.intern.status, InternStatus.WAITING)
        project = Project.objects.create(name="Балажан", recruitment_open=True)
        self.client.post(
            reverse("interns:project_add", args=[self.intern.pk]),
            {"project": project.pk, "comment": ""},
        )
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.status, InternStatus.ACTIVE)

    def test_add_to_project_does_not_override_other_status(self):
        """Если статус уже осмысленный (например «Приостановлен»), не
        затираем его молча."""
        from apps.projects.models import Project

        self.intern.status = InternStatus.PAUSED
        self.intern.save(update_fields=["status", "updated_at"])
        project = Project.objects.create(name="Балажан", recruitment_open=True)
        self.client.post(
            reverse("interns:project_add", args=[self.intern.pk]),
            {"project": project.pk, "comment": ""},
        )
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.status, InternStatus.PAUSED)

    def test_remove_from_project(self):
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        project = Project.objects.create(name="Балажан", recruitment_open=True)
        member = TeamMember.objects.create(
            project=project, intern=self.intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        response = self.client.post(
            reverse("interns:project_remove", args=[self.intern.pk, member.pk]),
            {"left_reason": "unresponsive"},
        )
        member.refresh_from_db()
        self.assertEqual(member.status, TeamMember.Status.LEFT)
        self.assertEqual(member.left_reason, "unresponsive")
        self.assertRedirects(response, self.intern.get_absolute_url())

    def test_cannot_remove_membership_belonging_to_another_intern(self):
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        other = Intern.objects.create(full_name="Другой Стажёр")
        project = Project.objects.create(name="Балажан", recruitment_open=True)
        member = TeamMember.objects.create(
            project=project, intern=other, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        response = self.client.post(
            reverse("interns:project_remove", args=[self.intern.pk, member.pk]),
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(TeamMember.objects.filter(pk=member.pk).exists())


class InternPauseTests(TestCase):
    """Заморозка стажировки руководителем (UI-03C): с причиной, сразу
    выход из всех активных проектов; возвращение — без восстановления участия."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.intern = Intern.objects.create(
            full_name="Модельбек Тестов", status=InternStatus.ACTIVE,
        )

    def test_pause_active_intern(self):
        response = self.client.post(reverse("interns:pause", args=[self.intern.pk]), {"reason": "Сессия"})
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.status, InternStatus.PAUSED)
        self.assertRedirects(response, self.intern.get_absolute_url())

    def test_pause_requires_reason(self):
        self.client.post(reverse("interns:pause", args=[self.intern.pk]), {"reason": "  "})
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.status, InternStatus.ACTIVE)

    def test_cannot_pause_dropped_intern(self):
        self.intern.status = InternStatus.DROPPED
        self.intern.save(update_fields=["status"])
        self.client.post(reverse("interns:pause", args=[self.intern.pk]), {"reason": "x"})
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.status, InternStatus.DROPPED)

    def test_unpause_paused_intern_waits_for_new_project(self):
        self.intern.status = InternStatus.PAUSED
        self.intern.save(update_fields=["status"])
        response = self.client.post(reverse("interns:unpause", args=[self.intern.pk]), {"reason": "Готов"})
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.status, InternStatus.WAITING)
        self.assertRedirects(response, self.intern.get_absolute_url())

    def test_cannot_unpause_non_paused_intern(self):
        response = self.client.post(reverse("interns:unpause", args=[self.intern.pk]), {"reason": "x"})
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.status, InternStatus.ACTIVE)
        self.assertRedirects(response, self.intern.get_absolute_url())

    def test_pause_writes_audit_log(self):
        from apps.audit.models import AuditLog

        self.client.post(reverse("interns:pause", args=[self.intern.pk]), {"reason": "Болезнь"})
        log = AuditLog.objects.get(object_type="Intern", object_id=str(self.intern.pk),
                                   action="Стажировка заморожена")
        self.assertEqual(log.reason, "Болезнь")

    def test_get_does_not_pause(self):
        self.assertEqual(self.client.get(reverse("interns:pause", args=[self.intern.pk])).status_code, 405)
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.status, InternStatus.ACTIVE)

    def test_detail_page_shows_pause_button_only_for_active(self):
        response = self.client.get(self.intern.get_absolute_url())
        self.assertContains(response, reverse("interns:pause", args=[self.intern.pk]))
        self.assertNotContains(response, reverse("interns:unpause", args=[self.intern.pk]))

        self.intern.status = InternStatus.PAUSED
        self.intern.save(update_fields=["status"])
        response = self.client.get(self.intern.get_absolute_url())
        self.assertContains(response, reverse("interns:unpause", args=[self.intern.pk]))
        self.assertNotContains(response, reverse("interns:pause", args=[self.intern.pk]))


class GrantPMAccessTests(TestCase):
    """Выдача логина ПМу со страницы стажёра."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        self.user = get_user_model().objects.create_user(
            username="head", password="x",
        )
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="Балажан", recruitment_open=True)
        self.pm = Intern.objects.create(full_name="Тестов ПМ")
        self.dev = Intern.objects.create(full_name="Тестов Бэкендер")
        TeamMember.objects.create(
            project=self.project, intern=self.pm, role=TeamRole.PROJECT_MANAGER,
            status=TeamMember.Status.ACTIVE,
        )
        TeamMember.objects.create(
            project=self.project, intern=self.dev, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )

    def test_button_shown_only_for_pm(self):
        pm_page = self.client.get(self.pm.get_absolute_url())
        self.assertContains(pm_page, "Выдать доступ")
        dev_page = self.client.get(self.dev.get_absolute_url())
        self.assertNotContains(dev_page, "Выдать доступ")

    def test_granting_access_creates_linked_user(self):
        from apps.accounts.models import User

        self.client.post(reverse("interns:grant_access", args=[self.pm.pk]), {
            "username": "+996700000099", "password": "somepass123",
        })
        self.pm.refresh_from_db()
        self.assertIsNotNone(self.pm.user)
        self.assertEqual(self.pm.user.username, "+996700000099")
        self.assertEqual(self.pm.user.role, User.Role.PROJECT_MANAGER)
        self.assertTrue(self.pm.user.check_password("somepass123"))

    def test_resetting_password_keeps_same_user(self):
        self.client.post(reverse("interns:grant_access", args=[self.pm.pk]), {
            "username": "+996700000099", "password": "firstpass123",
        })
        self.pm.refresh_from_db()
        first_user_id = self.pm.user_id

        self.client.post(reverse("interns:grant_access", args=[self.pm.pk]), {
            "username": "+996700000099", "password": "secondpass123",
        })
        self.pm.refresh_from_db()
        self.assertEqual(self.pm.user_id, first_user_id)
        self.assertTrue(self.pm.user.check_password("secondpass123"))


class GrantLeadAccessTests(TestCase):
    """Выдача логина тимлиду со страницы стажёра — тот же принцип, что
    и у ПМ, но своя роль/портал."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        self.user = get_user_model().objects.create_user(
            username="head3", password="x",
        )
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="Кундолук")
        self.lead = Intern.objects.create(full_name="Тестов Тимлид")
        self.dev = Intern.objects.create(full_name="Тестов Фронтендер")
        TeamMember.objects.create(
            project=self.project, intern=self.lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        TeamMember.objects.create(
            project=self.project, intern=self.dev, role=TeamRole.FRONTEND,
            status=TeamMember.Status.ACTIVE,
        )

    def test_button_shown_only_for_lead(self):
        lead_page = self.client.get(self.lead.get_absolute_url())
        self.assertContains(lead_page, "Выдать доступ тимлида")
        dev_page = self.client.get(self.dev.get_absolute_url())
        self.assertNotContains(dev_page, "Выдать доступ тимлида")

    def test_granting_access_creates_linked_user_with_lead_role(self):
        from apps.accounts.models import User

        self.client.post(reverse("interns:grant_lead_access", args=[self.lead.pk]), {
            "username": "+996700000098", "password": "somepass123",
        })
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.user)
        self.assertEqual(self.lead.user.username, "+996700000098")
        self.assertEqual(self.lead.user.role, User.Role.TEAM_LEAD)
        self.assertTrue(self.lead.user.check_password("somepass123"))

    def test_resetting_password_keeps_same_user(self):
        self.client.post(reverse("interns:grant_lead_access", args=[self.lead.pk]), {
            "username": "+996700000098", "password": "firstpass123",
        })
        self.lead.refresh_from_db()
        first_user_id = self.lead.user_id

        self.client.post(reverse("interns:grant_lead_access", args=[self.lead.pk]), {
            "username": "+996700000098", "password": "secondpass123",
        })
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.user_id, first_user_id)
        self.assertTrue(self.lead.user.check_password("secondpass123"))


class ResumeBankListTests(TestCase):
    """«Банк резюме» — включая тимлидов, в отличие от общего списка
    стажёров."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(username="head", password="x")
        self.client.force_login(self.user)

    def test_resume_bank_list_shows_only_flagged(self):
        Intern.objects.create(full_name="Без резюме")
        in_bank = Intern.objects.create(full_name="С резюме", in_resume_bank=True)
        response = self.client.get(reverse("interns:resume_bank"))
        names = [p.full_name for p in response.context["people"]]
        self.assertEqual(names, ["С резюме"])

    def test_team_leads_included_unlike_general_list(self):
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        project = Project.objects.create(name="Балажан", recruitment_open=True)
        lead = Intern.objects.create(full_name="Тимлид Резюмешный", in_resume_bank=True)
        TeamMember.objects.create(
            project=project, intern=lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        general = self.client.get(reverse("interns:list"))
        self.assertNotContains(general, "Тимлид Резюмешный")
        bank = self.client.get(reverse("interns:resume_bank"))
        self.assertContains(bank, "Тимлид Резюмешный")


class ResumeBankApplyTests(TestCase):
    """Публичная анкета «Банк резюме» — без входа в систему."""

    def test_anonymous_can_submit(self):
        response = self.client.post(reverse("resume_bank_apply"), {
            "full_name": "Новый Человек", "phone": "0700111222",
            "email": "new@example.com",
        })
        self.assertEqual(response.status_code, 200)
        intern = Intern.objects.get(phone="0700111222")
        self.assertTrue(intern.in_resume_bank)
        self.assertEqual(intern.full_name, "Новый Человек")
        self.assertEqual(intern.resume_bank_status, ResumeBankStatus.PENDING)

    def test_existing_person_by_phone_is_updated_not_duplicated(self):
        Intern.objects.create(full_name="Старое Имя", phone="0700111222")
        self.client.post(reverse("resume_bank_apply"), {
            "full_name": "Новое Имя", "phone": "0700111222",
            "email": "updated@example.com",
        })
        self.assertEqual(Intern.objects.filter(phone="0700111222").count(), 1)
        intern = Intern.objects.get(phone="0700111222")
        self.assertEqual(intern.full_name, "Новое Имя")
        self.assertTrue(intern.in_resume_bank)

    def test_phone_is_required(self):
        response = self.client.post(reverse("resume_bank_apply"), {
            "full_name": "Без Телефона",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Intern.objects.filter(full_name="Без Телефона").exists())

    def test_second_submission_in_same_session_is_blocked(self):
        self.client.post(reverse("resume_bank_apply"), {
            "full_name": "Первый", "phone": "0700111333",
        })
        self.client.post(reverse("resume_bank_apply"), {
            "full_name": "Второй", "phone": "0700111444",
        })
        self.assertFalse(Intern.objects.filter(phone="0700111444").exists())


class ResumeBankReviewTests(TestCase):
    """«Принять»/«На доработку» в списке банка резюме — руководитель
    решает, выпускнику уходит уведомление в Telegram."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(username="head2", password="x")
        self.client.force_login(self.user)
        self.intern = Intern.objects.create(
            full_name="На проверке", in_resume_bank=True,
            resume_bank_status=ResumeBankStatus.PENDING, telegram_chat_id=555,
        )

    def test_approve_requires_login(self):
        from django.test import Client

        anon = Client()
        response = anon.post(reverse("interns:resume_bank_approve", args=[self.intern.pk]))
        self.assertNotEqual(response.status_code, 200)
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.resume_bank_status, ResumeBankStatus.PENDING)

    def test_approve_sets_status_and_notifies(self):
        from unittest import mock

        with mock.patch("apps.graduate_bot.bot.bot.send_message") as send:
            self.client.post(reverse("interns:resume_bank_approve", args=[self.intern.pk]))
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.resume_bank_status, ResumeBankStatus.APPROVED)
        send.assert_called_once()

    def test_revise_requires_comment(self):
        from unittest import mock

        with mock.patch("apps.graduate_bot.bot.bot.send_message") as send:
            self.client.post(
                reverse("interns:resume_bank_revise", args=[self.intern.pk]), {"comment": ""},
            )
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.resume_bank_status, ResumeBankStatus.PENDING)
        send.assert_not_called()

    def test_revise_sets_status_comment_and_notifies(self):
        from unittest import mock

        with mock.patch("apps.graduate_bot.bot.bot.send_message") as send:
            self.client.post(
                reverse("interns:resume_bank_revise", args=[self.intern.pk]),
                {"comment": "Добавьте фото"},
            )
        self.intern.refresh_from_db()
        self.assertEqual(self.intern.resume_bank_status, ResumeBankStatus.REVISION)
        self.assertEqual(self.intern.resume_bank_comment, "Добавьте фото")
        self.assertIn("Добавьте фото", send.call_args[0][1])


class ProfileApplyTests(TestCase):
    """Публичная анкета профиля стажёра — без входа в систему.

    Анкета живёт по сменяемой ссылке: перед каждым тестом выпускаем её.
    """

    def setUp(self):
        from apps.training.models import Specialization

        self.spec = Specialization.objects.create(name="Backend")
        self.link = services.issue_profile_form_link()
        self.url = reverse("intern_profile_apply", args=[self.link.token])

    def _payload(self, **overrides):
        """Анкета теперь полностью обязательна — тут все поля разом,
        тесты переопределяют только то, что проверяют."""
        data = {
            "full_name": "Тестов Тест", "phone": "0700000001",
            "email": "test@example.com", "telegram": "@test",
            "city": "Бишкек", "branch": "Бишкек",
            "specialization": self.spec.pk,
            "education_end_date": "2026-06-01",
            "internship_start_date": "2026-07-01",
            "internship_attempt": "1",
        }
        data.update(overrides)
        return data

    def test_anonymous_can_submit_full_profile(self):
        response = self.client.post(self.url, self._payload(
            full_name="Новый Стажёр", phone="0700333444",
            email="n@example.com", telegram="@newintern",
            city="Бишкек", branch="Ош", internship_attempt="2",
        ))
        self.assertEqual(response.status_code, 200)
        intern = Intern.objects.get(phone="0700333444")
        self.assertEqual(intern.full_name, "Новый Стажёр")
        self.assertEqual(intern.telegram, "@newintern")
        self.assertEqual(intern.branch, "Ош")
        self.assertEqual(intern.internship_attempt, 2)

    def test_missing_field_rejects_the_whole_submission(self):
        payload = self._payload(full_name="Без Специализации", phone="0700333555")
        del payload["specialization"]
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Intern.objects.filter(phone="0700333555").exists())

    def test_existing_person_by_phone_is_updated_not_duplicated(self):
        Intern.objects.create(full_name="Старое Имя", phone="0700333444")
        self.client.post(self.url, self._payload(
            full_name="Новое Имя", phone="0700333444", city="Ош",
        ))
        self.assertEqual(Intern.objects.filter(phone="0700333444").count(), 1)
        intern = Intern.objects.get(phone="0700333444")
        self.assertEqual(intern.full_name, "Новое Имя")
        self.assertEqual(intern.city, "Ош")

    def test_existing_person_without_phone_matched_by_full_name(self):
        """У старой карточки (только ФИО, телефон не заполнен) не должно
        появиться дубля, когда стажёр сам заполняет анкету."""
        old = Intern.objects.create(full_name="Демобай Демобаев")
        self.client.post(self.url, self._payload(
            full_name="Демобай Демобаев", phone="0700555666", city="Бишкек",
        ))
        self.assertEqual(Intern.objects.filter(full_name="Демобай Демобаев").count(), 1)
        old.refresh_from_db()
        self.assertEqual(old.phone, "0700555666")
        self.assertEqual(old.city, "Бишкек")

    def test_name_match_is_case_insensitive(self):
        Intern.objects.create(full_name="демобай демобаев")
        self.client.post(self.url, self._payload(
            full_name="Демобай Демобаев", phone="0700555666",
        ))
        self.assertEqual(Intern.objects.filter(phone="0700555666").count(), 1)

    def test_name_match_skipped_when_existing_record_has_a_phone(self):
        """Если у старой записи уже есть телефон, совпадение по ФИО не
        используется — не хотим случайно склеить двух разных людей."""
        Intern.objects.create(full_name="Демобай Демобаев", phone="0700111111")
        self.client.post(self.url, self._payload(
            full_name="Демобай Демобаев", phone="0700555666",
        ))
        self.assertEqual(Intern.objects.filter(full_name="Демобай Демобаев").count(), 2)

    def test_phone_is_required(self):
        payload = self._payload(full_name="Без Телефона")
        del payload["phone"]
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Intern.objects.filter(full_name="Без Телефона").exists())

    def test_default_internship_attempt_is_one(self):
        intern = Intern.objects.create(full_name="Дефолтный")
        self.assertEqual(intern.internship_attempt, 1)

    def test_branch_only_accepts_the_two_offices(self):
        response = self.client.post(self.url, self._payload(
            full_name="Тест Филиал", phone="0700999888",
            branch="Какой-то другой офис",
        ))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Intern.objects.filter(phone="0700999888").exists())

    def test_second_submission_in_same_session_is_blocked(self):
        self.client.post(self.url, self._payload(
            full_name="Первый", phone="0700111333",
        ))
        self.client.post(self.url, self._payload(
            full_name="Второй", phone="0700111444",
        ))
        self.assertFalse(Intern.objects.filter(phone="0700111444").exists())


class ReserveResumeBankToggleTests(TestCase):
    """Карточка стажёра: «В резерв кадров» — только ссылка в отдельный
    пул (без тумблера на самом Intern); «Банк резюме» — только через
    публичную анкету, тут лишь показывается бейджем."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(username="head2", password="x")
        self.client.force_login(self.user)
        self.intern = Intern.objects.create(full_name="Тестов Тумблер")

    def test_resume_bank_shown_as_plain_badge_not_toggle(self):
        """Флаг банка резюме выставляется только формой — карточка его
        просто отображает, не даёт менять руками."""
        response = self.client.get(self.intern.get_absolute_url())
        badge = '<span class="gp-badge gp-badge--info">Банк резюме</span>'
        self.assertNotContains(response, badge)

        self.intern.in_resume_bank = True
        self.intern.save()
        response = self.client.get(self.intern.get_absolute_url())
        self.assertContains(response, badge)
        self.assertNotContains(response, 'name="in_resume_bank"')


class GraduatesListTests(TestCase):
    """«Выпускники»: стажёры завершённого проекта, ждущие проверки ПМ.

    Источник истины — ``Intern.graduate_status``, его выставляет
    :func:`apps.projects.services.release_team` при завершении проекта
    (см. `GraduateWorkflowTests` ниже) — здесь он проставляется вручную,
    чтобы проверить именно отображение списка.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(username="head3", password="x")
        self.client.force_login(self.user)

    def test_shows_intern_from_completed_project_only(self):
        import datetime

        from apps.projects.models import Project, ProjectStatus
        from apps.teams.models import TeamMember, TeamRole

        finished = Project.objects.create(
            name="Завершённый", status=ProjectStatus.COMPLETED,
            actual_end_date=datetime.date(2026, 1, 15),
        )
        active = Project.objects.create(name="Активный", status=ProjectStatus.ACTIVE)

        graduate = Intern.objects.create(
            full_name="Выпускник Один", graduate_status=GraduateStatus.PENDING,
        )
        TeamMember.objects.create(
            project=finished, intern=graduate, role=TeamRole.BACKEND,
            status=TeamMember.Status.LEFT, left_at=datetime.date(2026, 1, 15),
        )
        still_working = Intern.objects.create(full_name="Ещё Работает")
        TeamMember.objects.create(
            project=active, intern=still_working, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )

        response = self.client.get(reverse("interns:graduates"))
        names = [p.full_name for p in response.context["page"].object_list]
        self.assertEqual(names, ["Выпускник Один"])
        self.assertContains(response, "Выпускник Один")

    def test_resume_bank_shown_read_only(self):
        import datetime

        from apps.projects.models import Project, ProjectStatus
        from apps.teams.models import TeamMember, TeamRole

        finished = Project.objects.create(
            name="Завершённый", status=ProjectStatus.COMPLETED,
            actual_end_date=datetime.date(2026, 1, 15),
        )
        graduate = Intern.objects.create(
            full_name="Выпускник Два", in_resume_bank=True,
            resume_bank_status=ResumeBankStatus.PENDING,
            graduate_status=GraduateStatus.DECLINED,
        )
        TeamMember.objects.create(
            project=finished, intern=graduate, role=TeamRole.BACKEND,
            status=TeamMember.Status.LEFT, left_at=datetime.date(2026, 1, 15),
        )

        response = self.client.get(reverse("interns:graduates"))
        self.assertContains(response, "Ожидает проверки")
        self.assertNotContains(response, reverse("interns:reserve_create"))


class GraduatesSummaryTests(TestCase):
    """«Выпускники» открываются числами; число ведёт к людям."""

    def setUp(self):
        import datetime

        from django.contrib.auth import get_user_model

        from apps.projects.models import Project, ProjectStatus
        from apps.teams.models import TeamMember, TeamRole
        from apps.training.models import Specialization

        self.user = get_user_model().objects.create_user(username="head6", password="x")
        self.client.force_login(self.user)
        self.url = reverse("interns:graduates")
        self.backend = Specialization.objects.create(name="Backend")
        self.design = Specialization.objects.create(name="UX/UI")
        self.balazhan = Project.objects.create(
            name="Балажан", status=ProjectStatus.COMPLETED,
            actual_end_date=datetime.date(2026, 9, 17),
        )
        self.bilim = Project.objects.create(
            name="БилимОрдо", status=ProjectStatus.COMPLETED,
            actual_end_date=datetime.date(2026, 9, 10),
        )

        def graduate(name, spec, project, status=GraduateStatus.PENDING, bank=False):
            person = Intern.objects.create(
                full_name=name, specialization=spec,
                graduate_status=status, in_resume_bank=bank,
            )
            TeamMember.objects.create(
                project=project, intern=person, role=TeamRole.BACKEND,
                status=TeamMember.Status.LEFT, left_at=project.actual_end_date,
            )
            return person

        graduate("Бэк Один", self.backend, self.balazhan)
        graduate("Бэк Два", self.backend, self.bilim)
        graduate("Бэк Три", self.backend, self.bilim, GraduateStatus.DECLINED, bank=True)
        graduate("Дизайнер", self.design, self.balazhan, GraduateStatus.DECLINED)
        graduate("Без Направления", None, self.balazhan)

    def _names(self, **params):
        return sorted(p.full_name for p in self.client.get(self.url, params).context["page"].object_list)

    def test_list_opens_with_everyone_confirmed(self):
        """UI-04A: список выпускников открывается сразу людьми (не числами)."""
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, "interns/graduates.html")
        self.assertEqual(response.context["total"], 5)
        self.assertEqual(len(self._names()), 5)

    def test_filter_by_direction_and_activity(self):
        self.assertEqual(self._names(specialization=self.backend.pk), ["Бэк Два", "Бэк Один", "Бэк Три"])
        self.assertEqual(self._names(specialization=self.backend.pk, activity="pending"), ["Бэк Два", "Бэк Один"])
        self.assertEqual(self._names(activity="bank"), ["Бэк Три"])
        self.assertEqual(self._names(activity="declined"), ["Дизайнер"])
        # старые ссылки ?status= продолжают работать
        self.assertEqual(self._names(status="pending"), ["Без Направления", "Бэк Два", "Бэк Один"])

    def test_search(self):
        self.assertEqual(self._names(q="дизай"), ["Дизайнер"])


class GraduateWorkflowTests(TestCase):
    """Полный цикл «выпускника»: завершение проекта → проверка ПМ (ТЗ,
    сформулировано пользователем: «если проект закончился автоматически
    переходит в выпускники ... если не хочет — в банк резюме»)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(username="head5", password="x")
        self.client.force_login(self.user)

    def test_completing_project_marks_interns_pending(self):
        from apps.projects.models import Project, ProjectStatus
        from apps.projects.services import release_team
        from apps.teams.models import TeamMember, TeamRole

        project = Project.objects.create(name="Проект", status=ProjectStatus.COMPLETED)
        intern = Intern.objects.create(full_name="Стажёр")
        TeamMember.objects.create(
            project=project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )

        release_team(project)

        intern.refresh_from_db()
        self.assertEqual(intern.graduate_status, GraduateStatus.PENDING)

    def test_completing_project_moves_active_intern_to_ready(self):
        """Раньше «Статус» так и оставался «Активный», хотя человек
        по факту свободен между проектами — список стажёров это скрывал."""
        from apps.projects.models import Project, ProjectStatus
        from apps.projects.services import release_team
        from apps.teams.models import TeamMember, TeamRole

        project = Project.objects.create(name="Проект", status=ProjectStatus.COMPLETED)
        intern = Intern.objects.create(full_name="Стажёр", status=InternStatus.ACTIVE)
        TeamMember.objects.create(
            project=project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )

        release_team(project)

        intern.refresh_from_db()
        self.assertEqual(intern.status, InternStatus.READY)

    def test_completing_project_does_not_unpause_intern(self):
        """Замороженный стажёр остаётся замороженным — завершение проекта
        не должно тихо его «размораживать»."""
        from apps.projects.models import Project, ProjectStatus
        from apps.projects.services import release_team
        from apps.teams.models import TeamMember, TeamRole

        project = Project.objects.create(name="Проект", status=ProjectStatus.COMPLETED)
        intern = Intern.objects.create(full_name="Стажёр", status=InternStatus.PAUSED)
        TeamMember.objects.create(
            project=project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )

        release_team(project)

        intern.refresh_from_db()
        self.assertEqual(intern.status, InternStatus.PAUSED)

    def test_completing_project_does_not_mark_team_lead_pending(self):
        """Тимлид — уже полноценный сотрудник, а не стажёр: завершение
        проекта не должно превращать его в «выпускника на проверке» и
        сбрасывать его «Статус» на «Готов к распределению»."""
        from apps.projects.models import Project, ProjectStatus
        from apps.projects.services import release_team
        from apps.teams.models import TeamMember, TeamRole

        project = Project.objects.create(name="Проект", status=ProjectStatus.COMPLETED)
        lead = Intern.objects.create(full_name="Тимлид", status=InternStatus.ACTIVE)
        TeamMember.objects.create(
            project=project, intern=lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )

        release_team(project)

        lead.refresh_from_db()
        self.assertEqual(lead.graduate_status, "")
        self.assertEqual(lead.status, InternStatus.ACTIVE)

    def test_completing_project_marks_pm_pending_like_regular_intern(self):
        """ПМ — такой же стажёр-выпускник, как остальные (в отличие от
        тимлида): исключать его из «Выпускников» не нужно."""
        from apps.projects.models import Project, ProjectStatus
        from apps.projects.services import release_team
        from apps.teams.models import TeamMember, TeamRole

        project = Project.objects.create(name="Проект", status=ProjectStatus.COMPLETED)
        pm = Intern.objects.create(full_name="ПМ", status=InternStatus.ACTIVE)
        TeamMember.objects.create(
            project=project, intern=pm, role=TeamRole.PROJECT_MANAGER,
            status=TeamMember.Status.ACTIVE,
        )

        release_team(project)

        pm.refresh_from_db()
        self.assertEqual(pm.graduate_status, GraduateStatus.PENDING)
        self.assertEqual(pm.status, InternStatus.READY)

    def test_team_lead_with_stale_graduate_status_excluded_from_list(self):
        """Подстраховка на случай уже испорченных старых данных (до этого
        исправления) — даже если graduate_status кем-то выставлен, тимлид
        не должен показываться в «Выпускниках»."""
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        lead = Intern.objects.create(
            full_name="Тимлид Со Статусом", graduate_status=GraduateStatus.PENDING,
        )
        project = Project.objects.create(name="Другой проект")
        TeamMember.objects.create(
            project=project, intern=lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        self.assertNotIn(lead, services.graduated_interns())

    def test_cancelling_project_does_not_mark_interns_pending(self):
        """Проект отменён/отказ клиента — это не «успешный выпуск»."""
        from apps.projects.models import Project, ProjectStatus
        from apps.projects.services import release_team
        from apps.teams.models import TeamMember, TeamRole

        project = Project.objects.create(name="Проект", status=ProjectStatus.CANCELLED)
        intern = Intern.objects.create(full_name="Стажёр")
        TeamMember.objects.create(
            project=project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )

        release_team(project)

        intern.refresh_from_db()
        self.assertEqual(intern.graduate_status, "")

    def test_assigning_new_project_clears_graduate_status(self):
        from apps.projects.models import Project

        graduate = Intern.objects.create(
            full_name="Продолжает", graduate_status=GraduateStatus.PENDING,
            status=InternStatus.ACTIVE,
        )
        new_project = Project.objects.create(name="Новый проект", recruitment_open=True)

        response = self.client.post(
            reverse("interns:project_add", args=[graduate.pk]),
            {"project": new_project.pk},
        )
        self.assertRedirects(response, graduate.get_absolute_url())

        graduate.refresh_from_db()
        self.assertEqual(graduate.graduate_status, "")
        self.assertNotIn(graduate, services.graduated_interns())

    def test_assigning_new_project_logs_graduate_status_reset(self):
        """Сброс статуса выпускника не должен происходить незаметно —
        должна остаться запись в истории карточки."""
        from apps.audit.models import AuditLog
        from apps.projects.models import Project

        graduate = Intern.objects.create(
            full_name="Продолжает", graduate_status=GraduateStatus.PENDING,
            status=InternStatus.ACTIVE,
        )
        new_project = Project.objects.create(name="Новый проект", recruitment_open=True)

        self.client.post(
            reverse("interns:project_add", args=[graduate.pk]),
            {"project": new_project.pk},
        )

        entry = AuditLog.objects.get(
            object_type="Intern", object_id=str(graduate.pk),
            action="Статус выпускника снят",
        )
        self.assertIn("Новый проект", entry.reason)
        self.assertEqual(entry.user, self.user)

        response = self.client.get(graduate.get_absolute_url())
        self.assertContains(response, "Статус выпускника снят")

    def test_join_project_from_form_logs_graduate_status_reset(self):
        """Тот же сброс статуса выпускника, но по анкете-ссылке проекта —
        тоже не должен проходить незаметно."""
        from apps.audit.models import AuditLog
        from apps.interns.models import ProfileFormLink
        from apps.projects.models import Project

        graduate = Intern.objects.create(
            full_name="Заполнил Анкету", graduate_status=GraduateStatus.PENDING,
            status=InternStatus.ACTIVE,
        )
        project = Project.objects.create(name="Проект по ссылке", recruitment_open=True)
        link = ProfileFormLink.objects.create(project=project)

        services.join_project_from_form(graduate, link)

        graduate.refresh_from_db()
        self.assertEqual(graduate.graduate_status, "")
        entry = AuditLog.objects.get(
            object_type="Intern", object_id=str(graduate.pk),
            action="Статус выпускника снят",
        )
        self.assertIn("Проект по ссылке", entry.reason)

    def test_decline_view_shows_copyable_instructions(self):
        graduate = Intern.objects.create(
            full_name="Копия Инструкции", graduate_status=GraduateStatus.DECLINED,
        )

        response = self.client.get(reverse("interns:graduates"), {"status": "declined"})
        self.assertContains(response, reverse("resume_bank_apply"))


class TalentReserveStaffTests(TestCase):
    """Резерв кадров — отдельная сущность, сотрудник ведёт карточки и
    приоритет вручную."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(username="head4", password="x")
        self.client.force_login(self.user)

    def test_create_candidate(self):
        response = self.client.post(reverse("interns:reserve_create"), {
            "full_name": "Кандидат Один", "phone": "0700123123",
            "email": "", "telegram": "", "city": "Бишкек",
            "desired_role": "Backend разработчик", "experience": "",
            "portfolio_link": "", "priority": "0", "comment": "",
        })
        self.assertTrue(
            TalentReserveCandidate.objects.filter(full_name="Кандидат Один").exists(),
        )
        self.assertRedirects(response, reverse("interns:reserve"))

    def test_create_prefills_from_query_params(self):
        response = self.client.get(
            reverse("interns:reserve_create") + "?full_name=Иван+Иванов&phone=0700000001",
        )
        self.assertContains(response, "Иван Иванов")
        self.assertContains(response, "0700000001")

    def test_update_candidate(self):
        candidate = TalentReserveCandidate.objects.create(full_name="Старое Имя")
        self.client.post(reverse("interns:reserve_update", args=[candidate.pk]), {
            "full_name": "Новое Имя", "phone": "", "email": "", "telegram": "",
            "city": "", "desired_role": "", "experience": "",
            "portfolio_link": "", "priority": "5", "comment": "",
        })
        candidate.refresh_from_db()
        self.assertEqual(candidate.full_name, "Новое Имя")
        self.assertEqual(candidate.priority, 5)

    def test_delete_candidate(self):
        candidate = TalentReserveCandidate.objects.create(full_name="Удаляемый")
        self.client.post(reverse("interns:reserve_delete", args=[candidate.pk]))
        self.assertFalse(TalentReserveCandidate.objects.filter(pk=candidate.pk).exists())

    def test_set_priority(self):
        candidate = TalentReserveCandidate.objects.create(full_name="Приоритетный")
        self.client.post(
            reverse("interns:reserve_set_priority", args=[candidate.pk]),
            {"priority": "10"},
        )
        candidate.refresh_from_db()
        self.assertEqual(candidate.priority, 10)

    def test_list_ordered_by_priority_descending(self):
        low = TalentReserveCandidate.objects.create(full_name="Низкий", priority=1)
        high = TalentReserveCandidate.objects.create(full_name="Высокий", priority=10)
        response = self.client.get(reverse("interns:reserve"))
        names = [c.full_name for c in response.context["candidates"]]
        self.assertEqual(names, ["Высокий", "Низкий"])


class TalentReserveApplyTests(TestCase):
    """Публичная анкета «Резерв кадров» — без входа в систему."""

    def test_anonymous_can_submit(self):
        response = self.client.post(reverse("talent_reserve_apply"), {
            "full_name": "Новый Кандидат", "phone": "0700444555",
            "email": "cand@example.com", "desired_role": "QA",
        })
        self.assertEqual(response.status_code, 200)
        candidate = TalentReserveCandidate.objects.get(phone="0700444555")
        self.assertEqual(candidate.full_name, "Новый Кандидат")
        self.assertEqual(candidate.desired_role, "QA")

    def test_existing_person_by_phone_is_updated_not_duplicated(self):
        TalentReserveCandidate.objects.create(
            full_name="Старое Имя", phone="0700444555",
        )
        self.client.post(reverse("talent_reserve_apply"), {
            "full_name": "Новое Имя", "phone": "0700444555",
        })
        self.assertEqual(
            TalentReserveCandidate.objects.filter(phone="0700444555").count(), 1,
        )
        candidate = TalentReserveCandidate.objects.get(phone="0700444555")
        self.assertEqual(candidate.full_name, "Новое Имя")

    def test_existing_person_without_phone_matched_by_full_name(self):
        old = TalentReserveCandidate.objects.create(full_name="Демобай Демобаев")
        self.client.post(reverse("talent_reserve_apply"), {
            "full_name": "Демобай Демобаев", "phone": "0700666777",
        })
        self.assertEqual(
            TalentReserveCandidate.objects.filter(full_name="Демобай Демобаев").count(), 1,
        )
        old.refresh_from_db()
        self.assertEqual(old.phone, "0700666777")

    def test_phone_is_required(self):
        response = self.client.post(reverse("talent_reserve_apply"), {
            "full_name": "Без Телефона",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            TalentReserveCandidate.objects.filter(full_name="Без Телефона").exists(),
        )

    def test_priority_and_comment_not_settable_from_public_form(self):
        """Публичная анкета не даёт кандидату выставить себе приоритет —
        это только для сотрудников."""
        from apps.interns.forms import TalentReserveApplyForm

        self.assertNotIn("priority", TalentReserveApplyForm.Meta.fields)
        self.assertNotIn("comment", TalentReserveApplyForm.Meta.fields)

    def test_second_submission_in_same_session_is_blocked(self):
        self.client.post(reverse("talent_reserve_apply"), {
            "full_name": "Первый", "phone": "0700111333",
        })
        self.client.post(reverse("talent_reserve_apply"), {
            "full_name": "Второй", "phone": "0700111444",
        })
        self.assertFalse(
            TalentReserveCandidate.objects.filter(phone="0700111444").exists(),
        )


class ProfileFormLinkTests(TestCase):
    """Ссылка на анкету — непостоянная: новая гасит предыдущую."""

    def setUp(self):
        from apps.training.models import Specialization

        self.user = User.objects.create_user(username="pm", password="pass12345")
        self.client.force_login(self.user)
        self.spec = Specialization.objects.create(name="Backend")

    def _payload(self, **overrides):
        data = {
            "full_name": "Тестов Тест", "phone": "0700000001",
            "email": "test@example.com", "telegram": "@test",
            "city": "Бишкек", "branch": "Бишкек",
            "specialization": self.spec.pk,
            "education_end_date": "2026-06-01",
            "internship_start_date": "2026-07-01",
            "internship_attempt": "1",
        }
        data.update(overrides)
        return data

    def test_old_permanent_url_no_longer_opens_the_form(self):
        response = self.client.get("/intern-profile/")
        self.assertEqual(response.status_code, 404)

    def test_new_link_deactivates_the_previous_one(self):
        old = services.issue_profile_form_link(self.user)
        self.client.post(reverse("interns:profile_link_create"))
        old.refresh_from_db()
        self.assertFalse(old.is_active)
        self.assertEqual(ProfileFormLink.objects.filter(is_active=True).count(), 1)

    def test_form_opens_only_by_active_link(self):
        link = services.issue_profile_form_link(self.user)
        self.client.logout()
        url = reverse("intern_profile_apply", args=[link.token])
        self.assertEqual(self.client.get(url).status_code, 200)
        link.deactivate()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_submission_by_dead_link_is_ignored(self):
        link = services.issue_profile_form_link(self.user)
        url = reverse("intern_profile_apply", args=[link.token])
        link.deactivate()
        self.client.logout()
        self.client.post(url, {
            "full_name": "Мимо Кассы", "phone": "0700777111",
            "internship_attempt": "1",
        })
        self.assertFalse(Intern.objects.filter(phone="0700777111").exists())

    def test_submission_counter_grows(self):
        link = services.issue_profile_form_link(self.user)
        self.client.logout()
        self.client.post(
            reverse("intern_profile_apply", args=[link.token]),
            self._payload(full_name="Считаемый", phone="0700777222"),
        )
        link.refresh_from_db()
        self.assertEqual(link.submissions, 1)

    def test_disable_link_from_the_list_page(self):
        link = services.issue_profile_form_link(self.user)
        self.client.post(reverse("interns:profile_link_disable"))
        link.refresh_from_db()
        self.assertFalse(link.is_active)

    def test_link_with_ttl_dies_after_its_term(self):
        from datetime import timedelta

        from django.utils import timezone

        self.client.post(reverse("interns:profile_link_create"), {"ttl_days": "1"})
        link = ProfileFormLink.objects.get(is_active=True)
        self.assertIsNotNone(link.expires_at)
        url = reverse("intern_profile_apply", args=[link.token])
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 200)

        ProfileFormLink.objects.filter(pk=link.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertIsNone(services.active_profile_form_link())

    def test_link_without_ttl_has_no_expiry(self):
        self.client.post(reverse("interns:profile_link_create"), {"ttl_days": ""})
        self.assertIsNone(ProfileFormLink.objects.get(is_active=True).expires_at)

    def test_submission_is_written_to_the_answers_log(self):
        link = services.issue_profile_form_link(self.user)
        self.client.logout()
        self.client.post(
            reverse("intern_profile_apply", args=[link.token]),
            self._payload(full_name="Журнальный", phone="0700777333"),
        )
        entry = ProfileFormSubmission.objects.get()
        self.assertEqual(entry.full_name, "Журнальный")
        self.assertTrue(entry.is_new)
        self.assertEqual(entry.intern, Intern.objects.get(phone="0700777333"))

    def test_answers_page_lists_submissions(self):
        link = services.issue_profile_form_link(self.user)
        ProfileFormSubmission.objects.create(
            link=link, full_name="Кто-то", phone="0700000000", is_new=True,
        )
        response = self.client.get(reverse("interns:profile_link_answers"))
        self.assertContains(response, "Кто-то")


class InternsByProjectTests(TestCase):
    """Страница «Стажёры по проектам»: где сколько людей."""

    def setUp(self):
        from apps.projects.models import Project, ProjectStatus
        from apps.teams.models import TeamMember, TeamRole
        from apps.training.models import Specialization

        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.url = reverse("interns:by_project")
        self.backend = Specialization.objects.create(name="Backend")
        self.design = Specialization.objects.create(name="UX/UI")
        self.alpha = Project.objects.create(name="Альфа")
        self.beta = Project.objects.create(name="Бета")
        self.done = Project.objects.create(
            name="Закрытый", status=ProjectStatus.COMPLETED,
        )
        self.dev = Intern.objects.create(
            full_name="Разработчик Один", specialization=self.backend,
        )
        self.designer = Intern.objects.create(
            full_name="Дизайнер Два", specialization=self.design,
        )
        self.lead = Intern.objects.create(full_name="Тимлид Три")
        self.free_person = Intern.objects.create(full_name="Свободный Четыре")
        TeamMember.objects.create(
            project=self.alpha, intern=self.dev, role=TeamRole.BACKEND,
        )
        TeamMember.objects.create(
            project=self.alpha, intern=self.designer, role=TeamRole.UXUI,
        )
        TeamMember.objects.create(
            project=self.alpha, intern=self.lead, role=TeamRole.TEAM_LEAD,
        )

    def _row(self, response, project):
        return next(
            row for row in response.context["rows"]
            if row["project"].pk == project.pk
        )

    def test_counts_interns_per_project(self):
        response = self.client.get(self.url)
        row = self._row(response, self.alpha)
        self.assertEqual(len(row["interns"]), 2)
        self.assertEqual({s["name"]: s["total"] for s in row["specs"]}, {"Backend": 1, "UX/UI": 1})

    def test_new_and_old_split_by_team_role(self):
        from apps.interns.models import GraduateStatus
        from apps.teams.models import TeamMember, TeamRole

        old = Intern.objects.create(
            full_name="Выпускник Пять", specialization=self.design,
            graduate_status=GraduateStatus.PENDING,
        )
        # специальность в профиле — UX/UI, но в команде он Backend
        TeamMember.objects.create(project=self.alpha, intern=old, role=TeamRole.BACKEND)
        response = self.client.get(self.url)
        row = self._row(response, self.alpha)
        self.assertEqual((row["new"], row["old"]), (2, 1))
        backend = next(s for s in row["specs"] if s["name"] == "Backend")
        self.assertEqual((backend["total"], backend["new"], backend["old"]), (2, 1, 1))
        self.assertEqual(response.context["on_projects_old"], 1)
        self.assertContains(response, "1 нов. / 1 стар.")

    def test_pm_is_not_counted_as_intern_and_lead_is_not_repeated(self):
        from apps.teams.models import TeamMember, TeamRole

        pm = Intern.objects.create(full_name="Пиэм Шесть")
        TeamMember.objects.create(project=self.alpha, intern=pm, role=TeamRole.PROJECT_MANAGER)
        TeamMember.objects.create(project=self.alpha, intern=self.lead, role=TeamRole.TEAM_LEAD)
        response = self.client.get(self.url)
        row = self._row(response, self.alpha)
        self.assertNotIn(pm, row["interns"])
        self.assertEqual(row["pms"], [pm])
        self.assertEqual(row["leads"], [self.lead])
        self.assertEqual(response.context["pm_count"], 1)

    def test_team_lead_counted_separately_not_as_intern(self):
        response = self.client.get(self.url)
        row = self._row(response, self.alpha)
        self.assertNotIn(self.lead, row["interns"])
        self.assertIn(self.lead, row["leads"])

    def test_project_without_team_is_shown_empty(self):
        response = self.client.get(self.url)
        row = self._row(response, self.beta)
        self.assertEqual(row["interns"], [])
        self.assertContains(response, "Бета")

    def test_person_on_two_projects_counted_in_both(self):
        from apps.teams.models import TeamMember, TeamRole

        TeamMember.objects.create(
            project=self.beta, intern=self.dev, role=TeamRole.BACKEND,
        )
        response = self.client.get(self.url)
        self.assertEqual(len(self._row(response, self.alpha)["interns"]), 2)
        self.assertEqual(len(self._row(response, self.beta)["interns"]), 1)
        # человек один, поэтому «на проектах» его считаем один раз
        self.assertEqual(response.context["on_projects"], 2)

    def test_free_interns_are_those_without_a_project(self):
        response = self.client.get(self.url)
        free = [intern.full_name for intern in response.context["free"]]
        self.assertIn("Свободный Четыре", free)
        self.assertNotIn("Разработчик Один", free)
        self.assertNotIn("Тимлид Три", free)

    def test_completed_project_shows_up_only_with_live_team(self):
        from apps.teams.models import TeamMember, TeamRole

        response = self.client.get(self.url)
        self.assertNotIn(
            self.done.pk, [row["project"].pk for row in response.context["rows"]],
        )
        TeamMember.objects.create(
            project=self.done, intern=self.free_person, role=TeamRole.BACKEND,
        )
        response = self.client.get(self.url)
        row = self._row(response, self.done)
        self.assertTrue(row["is_closed"])
        self.assertContains(response, "проект закрыт, команда не снята")

    def test_left_members_are_not_counted(self):
        from apps.teams.models import TeamMember, TeamRole

        TeamMember.objects.create(
            project=self.beta, intern=self.free_person, role=TeamRole.BACKEND,
            status=TeamMember.Status.LEFT,
        )
        response = self.client.get(self.url)
        self.assertEqual(self._row(response, self.beta)["interns"], [])


class InternListAvailabilityTests(TestCase):
    """«Свободен» в списке — только тот, кого можно занять: без проекта и
    в рабочем статусе. Заморозка и выпускники — со своими метками."""

    def setUp(self):
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole
        from apps.training.models import Specialization

        self.user = User.objects.create_user(username="head-av", password="x")
        self.client.force_login(self.user)
        spec = Specialization.objects.create(name="Backend")
        project = Project.objects.create(name="Омур")
        self.busy = Intern.objects.create(
            full_name="Занятый", specialization=spec, status=InternStatus.ACTIVE,
        )
        TeamMember.objects.create(
            project=project, intern=self.busy, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        self.free = Intern.objects.create(
            full_name="Свободный", specialization=spec, status=InternStatus.ACTIVE,
        )
        self.paused = Intern.objects.create(
            full_name="Замороженный", specialization=spec, status=InternStatus.PAUSED,
        )
        self.graduate = Intern.objects.create(
            full_name="Выпускник", specialization=spec, status=InternStatus.ACTIVE,
            graduate_status=GraduateStatus.PENDING,
        )
        self.waiting = Intern.objects.create(
            full_name="Ожидает", specialization=spec, status=InternStatus.WAITING,
        )
        self.url = reverse("interns:list")

    def _names(self, **params):
        response = self.client.get(self.url, params)
        return [p.full_name for p in response.context["page"].object_list]

    def test_free_filter_skips_paused_graduates_and_waiting(self):
        self.assertEqual(self._names(availability="free"), ["Свободный"])

    def test_separate_filters(self):
        self.assertEqual(self._names(availability="busy"), ["Занятый"])
        self.assertEqual(self._names(availability="paused"), ["Замороженный"])
        self.assertEqual(self._names(availability="graduates"), ["Выпускник"])
        self.assertEqual(self._names(availability="waiting"), ["Ожидает"])

    def test_list_shows_status_and_projects(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Омур")
        self.assertContains(response, "Приостановлен")
        self.assertContains(response, "Ожидает стажировки")


class PositionPromotionTests(TestCase):
    """Должность — свойство человека: повысили один раз, и он тимлид
    на всех проектах, пока его не сняли."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole
        from apps.training.models import Specialization

        self.user = get_user_model().objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.frontend = Specialization.objects.create(name="Frontend")
        self.person = Intern.objects.create(
            full_name="Растущий Стажёр", specialization=self.frontend,
        )
        self.first = Project.objects.create(name="Олимпийская школа")
        self.second = Project.objects.create(name="Палароид")
        for project in (self.first, self.second):
            TeamMember.objects.create(
                project=project, intern=self.person, role=TeamRole.FRONTEND,
                status=TeamMember.Status.ACTIVE,
            )

    def _promote(self):
        return self.client.post(reverse("interns:promote_lead", args=[self.person.pk]))

    def test_promotion_sets_position_and_all_projects(self):
        from apps.teams.models import TeamMember, TeamRole

        self._promote()
        self.person.refresh_from_db()
        self.assertEqual(self.person.position, "lead")
        # этап 6: участия стажёра закрыты с причиной «повышен», проекты тимлида — отдельно
        self.assertEqual(set(TeamMember.objects.filter(intern=self.person).values_list("left_reason", flat=True)),
                         {TeamMember.LeftReason.PROMOTED})
        self.assertFalse(TeamMember.objects.filter(intern=self.person, status="active").exists())
        self.assertEqual(self.person.staff_status, "active")

    def test_new_project_gets_lead_role_automatically(self):
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        self._promote()
        third = Project.objects.create(name="Новый проект", recruitment_open=True)
        self.client.post(
            reverse("interns:project_add", args=[self.person.pk]),
            {"project": third.pk, "workload": 100},
        )
        member = TeamMember.objects.get(intern=self.person, project=third)
        self.assertEqual(member.role, TeamRole.TEAM_LEAD)

    def test_demotion_returns_to_own_direction(self):
        from apps.teams.models import TeamMember, TeamRole

        self._promote()
        self.client.post(reverse("interns:demote_lead", args=[self.person.pk]))
        self.person.refresh_from_db()
        self.assertEqual(self.person.position, "intern")
        roles = set(
            TeamMember.objects.filter(intern=self.person).values_list("role", flat=True),
        )
        self.assertEqual(roles, {TeamRole.FRONTEND})

    def test_card_shows_position_and_button(self):
        response = self.client.get(self.person.get_absolute_url())
        self.assertContains(response, "Повысить до тимлида")
        self._promote()
        response = self.client.get(self.person.get_absolute_url())
        self.assertContains(response, "Снять с тимлида")
        self.assertEqual(response.context["kind"], "Тимлид")

    def test_lead_without_projects_is_not_counted_as_intern(self):
        from apps.teams.selectors import staff_intern_ids

        person = Intern.objects.create(full_name="Тимлид Без Проектов", position="lead")
        self.assertIn(person.pk, staff_intern_ids())

    def test_promotion_is_written_to_history(self):
        from apps.audit.models import AuditLog

        self._promote()
        entry = AuditLog.objects.filter(
            object_id=str(self.person.pk), action="Повышен(а) до тимлида",
        ).first()
        self.assertIsNotNone(entry)
        self.assertIn("Олимпийская школа", entry.reason)

    def test_second_promotion_changes_nothing(self):
        self._promote()
        response = self._promote()
        self.assertEqual(response.status_code, 302)
        self.person.refresh_from_db()
        self.assertEqual(self.person.position, "lead")

    def test_backfill_command_sets_positions(self):
        from io import StringIO

        from django.core.management import call_command
        from apps.teams.models import TeamMember, TeamRole

        TeamMember.objects.filter(intern=self.person).update(role=TeamRole.TEAM_LEAD)
        out = StringIO()
        call_command("backfill_positions", stdout=out)
        self.person.refresh_from_db()
        self.assertEqual(self.person.position, "intern")
        self.assertIn("Будет изменено: 1", out.getvalue())
        call_command("backfill_positions", apply=True, stdout=StringIO())
        self.person.refresh_from_db()
        self.assertEqual(self.person.position, "lead")


class PastProjectsLabelTests(TestCase):
    """«Прошлые проекты» — это проекты, где человек больше не в команде.
    Сам проект при этом может идти дальше: заголовок не должен врать,
    что он завершён."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.projects.models import Project, ProjectStatus
        from apps.teams.models import TeamMember, TeamRole

        user = get_user_model().objects.create_user(username="head", password="x")
        self.client.force_login(user)
        self.person = Intern.objects.create(full_name="Сюжетбаева Йотагуль")
        self.running = Project.objects.create(name="ArtDream")
        self.done = Project.objects.create(
            name="Балажан", status=ProjectStatus.COMPLETED,
        )
        for project in (self.running, self.done):
            TeamMember.objects.create(
                project=project, intern=self.person, role=TeamRole.BACKEND,
                status=TeamMember.Status.LEFT,
            )

    def test_section_is_called_past_not_completed(self):
        response = self.client.get(self.person.get_absolute_url())
        self.assertContains(response, "Прошлые проекты")
        self.assertNotContains(response, "Завершённые проекты")

    def test_running_project_marked_as_left_team(self):
        response = self.client.get(self.person.get_absolute_url())
        self.assertContains(response, "вышел(а) из команды")

    def test_finished_project_marked_as_delivered(self):
        response = self.client.get(self.person.get_absolute_url())
        self.assertContains(response, "проект сдан")


class BotStartedLabelTests(TestCase):
    """11F: Telegram ID из 1.0 ≠ подключение к боту 2.0."""

    def test_legacy_id_is_not_connected_until_bot_used(self):
        from apps.interns import people
        from apps.tgbot.models import OutboundMessage, TelegramUpdate

        legacy = Intern.objects.create(full_name='Из Старого Бота', telegram_chat_id=7001)
        started = Intern.objects.create(full_name='Запустил Бота', telegram_chat_id=7002)
        sent = Intern.objects.create(full_name='Получил Сообщение', telegram_chat_id=7003)
        none = Intern.objects.create(full_name='Без Телеграма')
        TelegramUpdate.objects.create(update_id=1, chat_id=7002, user_id=7002, kind='message')
        TelegramUpdate.objects.create(update_id=2, chat_id=-100, user_id=7001, kind='message')  # в группе — не считается
        OutboundMessage.objects.create(kind='reply', chat_id=7003, text='x', status=OutboundMessage.Status.SENT)
        OutboundMessage.objects.create(kind='reply', chat_id=7001, text='x', status=OutboundMessage.Status.BOT_BLOCKED)
        qs = people.list_queryset(staff=set())

        def ids(value):
            return set(people.apply_filters(qs, {'telegram': value}, set()).values_list('pk', flat=True))

        self.assertEqual(ids('yes'), {started.pk, sent.pk})
        self.assertEqual(ids('legacy'), {legacy.pk})
        self.assertEqual(ids('no'), {legacy.pk, none.pk})
        self.assertFalse(people.card(legacy)['bot_started'])
        self.assertTrue(people.card(started)['bot_started'])


class RegistrationsPageTests(TestCase):
    """11F: «Регистрации» — воронка бота и шаг каждого нового."""

    def test_funnel_and_people(self):
        from apps.accounts.models import User
        from apps.interns.models import InternshipRequest
        from apps.tgbot.models import TelegramUpdate

        head = User.objects.create_user(username='head-reg', password='x', role=User.Role.HEAD)
        for n, outcome in enumerate(['reg:start', 'reg:name', 'reg:start', 'reg:name', 'reg:phone', 'reg:city', 'reg:done']):
            TelegramUpdate.objects.create(update_id=10_000 + n, chat_id=111 if n < 2 else 222, outcome=outcome)
        person = Intern.objects.create(full_name='Новый Регистр', telegram_chat_id=222, status='waiting')
        InternshipRequest.objects.create(intern=person, kind=InternshipRequest.Kind.ENROLL, reason='бот',
                                         sales_status=InternshipRequest.Sales.PENDING)
        self.client.force_login(head)
        page = self.client.get(reverse('interns:registrations'))
        self.assertEqual(page.status_code, 200)
        steps = {s['label']: s['count'] for s in page.context['steps']}
        self.assertEqual((steps['Начали регистрацию'], steps['Завершили регистрацию']), (2, 1))
        self.assertContains(page, 'Новый Регистр')
        self.assertContains(page, 'Ждёт подтверждения продаж')
        self.assertEqual([s['chat_id'] for s in page.context['stuck']], [111])  # начал и бросил на ФИО

from unittest import mock

from django.test import TestCase

from apps.graduate_bot import services
from apps.interns.models import GraduateStatus, Intern, InternStatus, ResumeBankStatus
from apps.projects.models import Project, ProjectStageKey, ProjectStatus
from apps.teams.models import TeamMember, TeamRole
from apps.training.models import Specialization


class FindGraduatesTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Балажан", status=ProjectStatus.COMPLETED)
        self.grad = Intern.objects.create(
            full_name="Шаблоналиева Примерана Эскизовна", graduate_status=GraduateStatus.PENDING,
        )
        TeamMember.objects.create(
            project=self.project, intern=self.grad, role=TeamRole.UXUI,
            status=TeamMember.Status.LEFT,
        )
        self.non_graduate = Intern.objects.create(full_name="Активный Стажёров")

    def test_finds_by_partial_name_case_insensitive(self):
        result = services.find_graduates("примерана")
        self.assertEqual([i.pk for i in result], [self.grad.pk])

    def test_finds_by_any_word_order(self):
        """Люди пишут «Имя Фамилия», «Фамилия Имя» или одно слово."""
        for typed in (
            "Шаблоналиева Примерана", "Примерана Шаблоналиева", "шаблоналиева",
            "  ПРИМЕРАНА   эскизовна ", "Шаб Примеран",
        ):
            with self.subTest(typed=typed):
                self.assertEqual(
                    [i.pk for i in services.find_graduates(typed)], [self.grad.pk],
                )

    def test_yo_and_extra_spaces_do_not_break_search(self):
        person = Intern.objects.create(
            full_name="Мокапов Тёмхан", graduate_status=GraduateStatus.PENDING,
        )
        TeamMember.objects.create(
            project=self.project, intern=person, role=TeamRole.BACKEND,
            status=TeamMember.Status.LEFT,
        )
        self.assertEqual(
            [i.pk for i in services.find_graduates("темхан  мокапов")], [person.pk],
        )

    def test_partly_wrong_name_shows_candidates(self):
        """Фамилию написали неверно — показываем похожих, а не тупик:
        дальше человек выбирает себя кнопкой."""
        found = services.find_graduates("Примерана Иванова")
        self.assertEqual([p.pk for p in found], [self.grad.pk])

    def test_completely_other_name_finds_nobody(self):
        self.assertEqual(services.find_graduates("Ноутенко Михаил"), [])

    def test_no_match_returns_empty(self):
        self.assertEqual(services.find_graduates("Неизвестный Человек"), [])

    def test_non_graduate_not_found(self):
        self.assertEqual(services.find_graduates("Активный"), [])

    def test_blank_name_returns_empty(self):
        self.assertEqual(services.find_graduates("   "), [])

    def test_team_lead_not_found_even_with_pending_status(self):
        """Тимлид — уже сотрудник, а не выпускник: бот не должен вести его
        через сценарий стажёра, даже если graduate_status как-то выставлен."""
        lead = Intern.objects.create(
            full_name="Тимлидов Фихан", graduate_status=GraduateStatus.PENDING,
        )
        TeamMember.objects.create(
            project=self.project, intern=lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        self.assertEqual(services.find_graduates("Тимлидов"), [])


class PhoneMatchesTests(TestCase):
    def setUp(self):
        self.intern = Intern.objects.create(full_name="Тест Тестов", phone="0700111222")

    def test_exact_match(self):
        self.assertTrue(services.phone_matches(self.intern, "0700111222"))

    def test_matches_with_country_code_and_formatting(self):
        self.assertTrue(services.phone_matches(self.intern, "+996 700 111 222"))
        self.assertTrue(services.phone_matches(self.intern, "996-700-111-222"))

    def test_mismatch(self):
        self.assertFalse(services.phone_matches(self.intern, "0700111223"))

    def test_too_short_does_not_match(self):
        self.assertFalse(services.phone_matches(self.intern, "1234"))

    def test_matches_with_parentheses_and_dots(self):
        self.assertTrue(services.phone_matches(self.intern, "+996 (700) 111.222"))

    def test_incomplete_stored_phone_still_matches_on_available_digits(self):
        """Если телефон в карточке когда-то занесли не полностью (меньше
        9 цифр), это не должно значить «никакой формат не подходит» —
        сверяем по тому, что реально есть."""
        intern = Intern.objects.create(full_name="Неполный Телефон", phone="111222")
        self.assertTrue(services.phone_matches(intern, "0700111222"))
        self.assertTrue(services.phone_matches(intern, "+996 700 111 222"))

    def test_very_short_stored_phone_does_not_match(self):
        intern = Intern.objects.create(full_name="Совсем Короткий", phone="222")
        self.assertFalse(services.phone_matches(intern, "0700111222"))

    def test_blank_stored_phone_never_matches(self):
        intern = Intern.objects.create(full_name="Без Телефона", phone="")
        self.assertFalse(services.phone_matches(intern, "0700111222"))


class CompletedProjectsTests(TestCase):
    def setUp(self):
        self.intern = Intern.objects.create(full_name="Выпускник Проектов")

    def test_lists_completed_project_memberships(self):
        project = Project.objects.create(name="Завершённый", status=ProjectStatus.COMPLETED)
        TeamMember.objects.create(
            project=project, intern=self.intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.LEFT,
        )
        result = services.completed_projects(self.intern)
        self.assertEqual([m.project for m in result], [project])

    def test_excludes_active_and_non_completed_projects(self):
        active = Project.objects.create(name="Активный", status=ProjectStatus.ACTIVE)
        TeamMember.objects.create(
            project=active, intern=self.intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        cancelled = Project.objects.create(name="Отменённый", status=ProjectStatus.CANCELLED)
        TeamMember.objects.create(
            project=cancelled, intern=self.intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.LEFT,
        )
        self.assertEqual(services.completed_projects(self.intern), [])


class RememberChatIdTests(TestCase):
    def test_saves_chat_id(self):
        intern = Intern.objects.create(full_name="Чат Идов")
        services.remember_chat_id(intern, 12345)
        intern.refresh_from_db()
        self.assertEqual(intern.telegram_chat_id, 12345)

    def test_noop_when_unchanged(self):
        intern = Intern.objects.create(full_name="Чат Идов", telegram_chat_id=12345)
        with mock.patch.object(Intern, "save") as save:
            services.remember_chat_id(intern, 12345)
            save.assert_not_called()


class FindByChatIdTests(TestCase):
    def test_finds_by_chat_id(self):
        intern = Intern.objects.create(full_name="Найденный", telegram_chat_id=999)
        self.assertEqual(services.find_by_chat_id(999), intern)

    def test_none_when_not_found(self):
        self.assertIsNone(services.find_by_chat_id(999))


class ResolvedStatusMessageTests(TestCase):
    def test_approved_message(self):
        intern = Intern.objects.create(
            full_name="Принятый", resume_bank_status=ResumeBankStatus.APPROVED,
        )
        self.assertIn("принято", services.resolved_status_message(intern))

    def test_revision_message_includes_comment(self):
        intern = Intern.objects.create(
            full_name="На доработке", resume_bank_status=ResumeBankStatus.REVISION,
            resume_bank_comment="Поправьте фото",
        )
        self.assertIn("Поправьте фото", services.resolved_status_message(intern))

    def test_pending_message(self):
        intern = Intern.objects.create(
            full_name="Ожидающий", resume_bank_status=ResumeBankStatus.PENDING,
        )
        self.assertIn("на проверке", services.resolved_status_message(intern))

    def test_fallback_message_for_project_join(self):
        intern = Intern.objects.create(full_name="В проекте")
        self.assertIn("продолжаете стажировку", services.resolved_status_message(intern))


class SubmitToResumeBankTests(TestCase):
    def test_sets_pending_status_and_flag(self):
        intern = Intern.objects.create(full_name="Выпускник Резюме")
        result = services.submit_to_resume_bank(intern, 777)
        intern.refresh_from_db()
        self.assertTrue(result)
        self.assertTrue(intern.in_resume_bank)
        self.assertEqual(intern.resume_bank_status, ResumeBankStatus.PENDING)
        self.assertEqual(intern.telegram_chat_id, 777)

    def test_clears_graduate_status(self):
        """Иначе find_graduates() снова находит человека после /start, и
        заявку можно переотправить бесконечно (см. test_does_not_downgrade)."""
        intern = Intern.objects.create(
            full_name="Выпускник Резюме", graduate_status=GraduateStatus.PENDING,
        )
        services.submit_to_resume_bank(intern, 777)
        intern.refresh_from_db()
        self.assertEqual(intern.graduate_status, "")

    def test_logs_to_audit(self):
        from apps.audit.models import AuditLog

        intern = Intern.objects.create(full_name="Выпускник Резюме")
        services.submit_to_resume_bank(intern, 777)
        self.assertTrue(
            AuditLog.objects.filter(
                object_type="Intern", object_id=str(intern.pk),
                action="Заявка в банк резюме отправлена",
            ).exists(),
        )

    def test_creates_notification_for_head_feed(self):
        from apps.notifications.models import Notification

        intern = Intern.objects.create(full_name="Выпускник Резюме")
        services.submit_to_resume_bank(intern, 777)
        notification = Notification.objects.get(dedup_key=f"resume-bank-submitted:{intern.pk}")
        self.assertIsNone(notification.intern)
        self.assertIn("Выпускник Резюме", notification.title)

    def test_second_submission_does_not_duplicate_open_notification(self):
        from apps.notifications.models import Notification

        intern = Intern.objects.create(
            full_name="Выпускник Резюме", resume_bank_status=ResumeBankStatus.REVISION,
        )
        services.submit_to_resume_bank(intern, 777)
        services.submit_to_resume_bank(intern, 777)
        self.assertEqual(
            Notification.objects.filter(
                dedup_key=f"resume-bank-submitted:{intern.pk}",
            ).count(),
            1,
        )

    def test_does_not_downgrade_already_approved(self):
        intern = Intern.objects.create(
            full_name="Уже принят", resume_bank_status=ResumeBankStatus.APPROVED,
        )
        result = services.submit_to_resume_bank(intern, 777)
        intern.refresh_from_db()
        self.assertFalse(result)
        self.assertEqual(intern.resume_bank_status, ResumeBankStatus.APPROVED)

    def test_allows_resubmit_from_revision(self):
        intern = Intern.objects.create(
            full_name="На доработке", resume_bank_status=ResumeBankStatus.REVISION,
            resume_bank_comment="Поправьте фото",
        )
        result = services.submit_to_resume_bank(intern, 777)
        intern.refresh_from_db()
        self.assertTrue(result)
        self.assertEqual(intern.resume_bank_status, ResumeBankStatus.PENDING)
        self.assertEqual(intern.resume_bank_comment, "")


class PhoneLockTests(TestCase):
    def test_not_locked_by_default(self):
        intern = Intern.objects.create(full_name="Незаблокированный")
        self.assertFalse(services.is_phone_locked(intern))
        self.assertEqual(services.phone_lock_minutes_left(intern), 0)

    def test_lock_sets_future_timestamp_and_blocks(self):
        intern = Intern.objects.create(full_name="Подбирающий Телефон")
        services.lock_phone_verification(intern)
        intern.refresh_from_db()
        self.assertTrue(services.is_phone_locked(intern))
        self.assertGreater(services.phone_lock_minutes_left(intern), 0)

    def test_lock_expires_in_the_past_does_not_block(self):
        from datetime import timedelta

        from django.utils import timezone

        intern = Intern.objects.create(
            full_name="Старая Блокировка",
            phone_lock_until=timezone.now() - timedelta(minutes=1),
        )
        self.assertFalse(services.is_phone_locked(intern))

    def test_logs_to_audit(self):
        from apps.audit.models import AuditLog

        intern = Intern.objects.create(full_name="Подбирающий Телефон")
        services.lock_phone_verification(intern)
        self.assertTrue(
            AuditLog.objects.filter(
                object_type="Intern", object_id=str(intern.pk),
                action="Бот-выпускник: проверка телефона заблокирована",
            ).exists(),
        )


class NotifyResumeBankDecisionTests(TestCase):
    def test_does_nothing_without_chat_id(self):
        intern = Intern.objects.create(full_name="Без чата")
        with mock.patch("apps.graduate_bot.bot.bot.send_message") as send:
            services.notify_resume_bank_decision(intern, approved=True)
        send.assert_not_called()

    def test_sends_congrats_when_approved(self):
        intern = Intern.objects.create(full_name="Принятый", telegram_chat_id=42)
        with mock.patch("apps.graduate_bot.bot.bot.send_message") as send:
            services.notify_resume_bank_decision(intern, approved=True)
        send.assert_called_once()
        self.assertEqual(send.call_args[0][0], 42)
        self.assertIn("Поздравляем", send.call_args[0][1])

    def test_sends_comment_when_revision(self):
        intern = Intern.objects.create(full_name="На доработке", telegram_chat_id=42)
        with mock.patch("apps.graduate_bot.bot.bot.send_message") as send:
            services.notify_resume_bank_decision(intern, approved=False, comment="Поправьте фото")
        self.assertIn("Поправьте фото", send.call_args[0][1])

    def test_swallows_send_errors(self):
        intern = Intern.objects.create(full_name="Сбой", telegram_chat_id=42)
        with mock.patch(
            "apps.graduate_bot.bot.bot.send_message", side_effect=Exception("boom"),
        ):
            services.notify_resume_bank_decision(intern, approved=True)


class TeamLeadContactTests(TestCase):
    def setUp(self):
        self.backend = Specialization.objects.create(name="Backend")
        self.frontend = Specialization.objects.create(name="Frontend")
        self.project = Project.objects.create(name="Проект")
        self.backend_lead = Intern.objects.create(
            full_name="Бэкенд Тимлид", specialization=self.backend,
        )
        self.frontend_lead = Intern.objects.create(
            full_name="Фронтенд Тимлид", specialization=self.frontend,
        )
        TeamMember.objects.create(
            project=self.project, intern=self.backend_lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        TeamMember.objects.create(
            project=self.project, intern=self.frontend_lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        self.graduate = Intern.objects.create(full_name="Выпускник", specialization=self.backend)

    def test_matches_lead_of_own_direction(self):
        contact = services.team_lead_contact(self.project, self.graduate)
        self.assertEqual(contact, self.backend_lead)

    def test_falls_back_to_any_lead_without_specialization(self):
        self.graduate.specialization = None
        self.graduate.save(update_fields=["specialization"])
        contact = services.team_lead_contact(self.project, self.graduate)
        self.assertIn(contact, [self.backend_lead, self.frontend_lead])

    def test_none_when_no_lead(self):
        empty_project = Project.objects.create(name="Без тимлида")
        self.assertIsNone(services.team_lead_contact(empty_project, self.graduate))


class BotActivityTests(TestCase):
    def test_excludes_people_who_never_used_the_bot(self):
        Intern.objects.create(full_name="Не писал боту")
        self.assertEqual(services.bot_activity(), [])

    def test_pending_outcome_for_unresolved_graduate(self):
        intern = Intern.objects.create(
            full_name="Ещё выбирает", telegram_chat_id=111,
            graduate_status=GraduateStatus.PENDING,
        )
        result = services.bot_activity()
        self.assertEqual([i.pk for i in result], [intern.pk])
        self.assertEqual(result[0].bot_outcome, "pending")

    def test_resume_bank_outcome(self):
        Intern.objects.create(
            full_name="В банк резюме", telegram_chat_id=222,
            resume_bank_status=ResumeBankStatus.PENDING,
        )
        result = services.bot_activity()
        self.assertEqual(result[0].bot_outcome, "resume_bank")

    def test_continued_outcome_with_current_project(self):
        project = Project.objects.create(name="Проект Продолжения")
        intern = Intern.objects.create(full_name="Продолжил", telegram_chat_id=333)
        TeamMember.objects.create(
            project=project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )
        result = services.bot_activity()
        self.assertEqual(result[0].bot_outcome, "continued")
        self.assertEqual(result[0].bot_project, project)

    def test_continued_outcome_without_current_team(self):
        """Например, потом сам ушёл с проекта — не должно падать."""
        intern = Intern.objects.create(full_name="Продолжил И Ушёл", telegram_chat_id=444)
        result = services.bot_activity()
        self.assertEqual(result[0].bot_outcome, "continued")
        self.assertIsNone(result[0].bot_project)


class ActivityListViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(username="head4", password="x")

    def test_requires_login(self):
        from django.urls import reverse

        response = self.client.get(reverse("graduate_bot:activity"))
        self.assertNotEqual(response.status_code, 200)

    def test_redirects_to_graduates(self):
        from django.urls import reverse

        self.client.force_login(self.user)
        response = self.client.get(reverse("graduate_bot:activity"))
        self.assertRedirects(response, reverse("interns:graduates"), fetch_redirect_response=False)


class WaitingForNextInternshipTests(TestCase):
    """Свободных проектов нет — выпускник записывается в очередь кнопкой,
    и мы фиксируем, что он продолжает стажировку."""

    def setUp(self):
        self.grad = Intern.objects.create(
            full_name="Макетбаев Пробобек", telegram_chat_id=777,
            graduate_status=GraduateStatus.PENDING,
        )

    def test_request_marks_person_as_waiting(self):
        self.assertTrue(services.request_next_internship(self.grad, 777))
        self.grad.refresh_from_db()
        self.assertEqual(self.grad.graduate_status, GraduateStatus.WAITING)

    def test_second_press_changes_nothing(self):
        services.request_next_internship(self.grad, 777)
        self.assertFalse(services.request_next_internship(self.grad, 777))

    def test_head_gets_notification(self):
        from apps.notifications.models import Notification

        services.request_next_internship(self.grad, 777)
        self.assertTrue(
            Notification.objects.filter(title__contains="Ждёт стажировку").exists()
        )

    def test_event_written_to_history(self):
        from apps.audit.models import AuditLog

        services.request_next_internship(self.grad, 777)
        self.assertTrue(
            AuditLog.objects.filter(
                object_id=str(self.grad.pk), action="Ждёт ближайшую стажировку",
            ).exists()
        )

    def test_chat_id_remembered(self):
        person = Intern.objects.create(
            full_name="Без чата", graduate_status=GraduateStatus.PENDING,
        )
        services.request_next_internship(person, 999)
        person.refresh_from_db()
        self.assertEqual(person.telegram_chat_id, 999)

    def test_start_message_tells_about_the_queue(self):
        services.request_next_internship(self.grad, 777)
        self.grad.refresh_from_db()
        text = services.resolved_status_message(self.grad)
        self.assertIn("базе ожидания", text)

    def test_activity_page_shows_waiting(self):
        services.request_next_internship(self.grad, 777)
        self.grad.refresh_from_db()
        result = services.bot_activity()
        self.assertEqual(result[0].bot_outcome, "waiting")


class WaitingGraduatesPageTests(TestCase):
    """Ждущих видно в «Выпускниках»: отдельная карточка и свой статус."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(username="head", password="x")
        self.client.force_login(user)
        self.project = Project.objects.create(name="Балажан", status=ProjectStatus.COMPLETED)
        self.grad = Intern.objects.create(
            full_name="Макетбаев Пробобек", graduate_status=GraduateStatus.WAITING,
            status=InternStatus.EMPLOYABLE,
        )
        TeamMember.objects.create(
            project=self.project, intern=self.grad, role=TeamRole.BACKEND,
            status=TeamMember.Status.LEFT,
        )

    def test_summary_counts_waiting(self):
        from django.urls import reverse

        response = self.client.get(reverse("interns:graduates"), {"activity": "waiting"})
        self.assertEqual(len(response.context["page"].object_list), 1)
        self.assertContains(response, "Ждёт следующий проект")

    def test_list_shows_status(self):
        from django.urls import reverse

        response = self.client.get(reverse("interns:graduates") + "?status=waiting")
        self.assertContains(response, "Макетбаев Пробобек")
        self.assertContains(response, "Ждёт следующий проект")


class BotTimeoutsTests(TestCase):
    """Через медленный прокси дорога до Telegram занимает 5–15 секунд:
    бюджет запроса должен быть заметно больше времени удержания
    соединения, иначе каждый опрос падает и бот выглядит молчащим."""

    def test_request_budget_is_bigger_than_long_polling(self):
        import inspect

        from apps.graduate_bot.management.commands import run_graduate_bot

        source = inspect.getsource(run_graduate_bot.Command.handle)
        self.assertIn("timeout=45", source)
        self.assertIn("long_polling_timeout=10", source)

    def test_api_timeouts_have_headroom(self):
        from telebot import apihelper

        from apps.graduate_bot import bot  # noqa: F401 — настройки ставятся при импорте

        self.assertGreaterEqual(apihelper.CONNECT_TIMEOUT, 30)
        self.assertGreaterEqual(apihelper.READ_TIMEOUT, 60)
        self.assertTrue(apihelper.RETRY_ON_ERROR)


class RulesAcceptanceTests(TestCase):
    """Правила читают один раз: отметка о чате переносится в карточку
    при входе в аккаунт."""

    def setUp(self):
        from apps.graduate_bot import services as bot_services

        bot_services._rules_accepted.clear()

    def test_rules_not_accepted_by_default(self):
        self.assertFalse(services.rules_accepted(555))

    def test_accept_is_remembered_for_chat(self):
        services.accept_rules(555)
        self.assertTrue(services.rules_accepted(555))

    def test_acceptance_lands_in_card_on_login(self):
        person = Intern.objects.create(
            full_name="Ознакомленный", graduate_status=GraduateStatus.PENDING,
        )
        services.accept_rules(555)
        services.remember_chat_id(person, 555)
        person.refresh_from_db()
        self.assertIsNotNone(person.rules_accepted_at)
        self.assertEqual(person.telegram_chat_id, 555)

    def test_card_mark_survives_bot_restart(self):
        from django.utils import timezone

        person = Intern.objects.create(
            full_name="Старый знакомый", rules_accepted_at=timezone.now(),
        )
        # перезапуск бота — память о чатах пустая
        from apps.graduate_bot import services as bot_services

        bot_services._rules_accepted.clear()
        self.assertTrue(services.rules_accepted(777, person))


class OneAccountPerChatTests(TestCase):
    """Один Telegram — один выпускник: за другого войти нельзя."""

    def setUp(self):
        self.first = Intern.objects.create(full_name="Первый Вошедший", telegram_chat_id=900)
        self.second = Intern.objects.create(full_name="Второй Желающий")

    def test_other_person_is_detected(self):
        taken = services.chat_taken_by_other(self.second, 900)
        self.assertEqual(taken, self.first)

    def test_own_chat_is_free(self):
        self.assertIsNone(services.chat_taken_by_other(self.first, 900))

    def test_new_chat_is_free(self):
        self.assertIsNone(services.chat_taken_by_other(self.second, 901))


class NotifyProjectAssignedTests(TestCase):
    """Ждавшему сообщаем, что место нашлось — это обещал бот."""

    def setUp(self):
        self.spec = Specialization.objects.create(name="Backend")
        self.project = Project.objects.create(name="Новый проект")
        self.person = Intern.objects.create(
            full_name="Ждавший Места", specialization=self.spec,
            telegram_chat_id=321, graduate_status=GraduateStatus.WAITING,
        )

    def test_message_names_project(self):
        with mock.patch("apps.graduate_bot.bot.bot.send_message") as send:
            services.notify_project_assigned(self.person, self.project)
        self.assertTrue(send.called)
        chat_id, text = send.call_args[0][0], send.call_args[0][1]
        self.assertEqual(chat_id, 321)
        self.assertIn("Новый проект", text)

    def test_lead_contact_included(self):
        lead = Intern.objects.create(full_name="Тимлид Бэкенд", specialization=self.spec, phone="0700112233")
        TeamMember.objects.create(
            project=self.project, intern=lead, role=TeamRole.TEAM_LEAD,
            status=TeamMember.Status.ACTIVE,
        )
        with mock.patch("apps.graduate_bot.bot.bot.send_message") as send:
            services.notify_project_assigned(self.person, self.project)
        text = send.call_args[0][1]
        self.assertIn("Тимлид Бэкенд", text)
        self.assertIn("0700112233", text)

    def test_person_without_chat_is_skipped(self):
        self.person.telegram_chat_id = None
        self.person.save(update_fields=["telegram_chat_id"])
        with mock.patch("apps.graduate_bot.bot.bot.send_message") as send:
            services.notify_project_assigned(self.person, self.project)
        self.assertFalse(send.called)

    def test_telegram_failure_does_not_break_assignment(self):
        with mock.patch("apps.graduate_bot.bot.bot.send_message", side_effect=Exception("boom")):
            services.notify_project_assigned(self.person, self.project)  # не падаем


class AssignmentFromPlatformTests(TestCase):
    """Распределение со страницы «Ожидают проект» — с сообщением в бота."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(username="head", password="x")
        self.client.force_login(user)
        self.spec = Specialization.objects.create(name="Backend")
        self.project = Project.objects.create(name="Новый проект", recruitment_open=True)
        self.person = Intern.objects.create(
            full_name="Ждавший Места", specialization=self.spec,
            telegram_chat_id=321, graduate_status=GraduateStatus.WAITING,
            status=InternStatus.READY,
        )

    def test_waiting_page_lists_person(self):
        from django.urls import reverse

        response = self.client.get(reverse("interns:waiting"))
        # 11F: «Ожидают проект» — вкладка «Решения по стажёрам» страницы «Telegram-бот»
        self.assertContains(response, "Решения по стажёрам")
        # 11F: стажёры попадают в проект только через бота — кнопки ручного распределения нет
        self.assertNotContains(response, reverse("interns:project_add", args=[self.person.pk]))

    def test_assignment_notifies_and_clears_status(self):
        from django.urls import reverse

        with mock.patch("apps.graduate_bot.bot.bot.send_message") as send:
            self.client.post(
                reverse("interns:project_add", args=[self.person.pk]),
                {"project": self.project.pk, "workload": 100},
            )
        self.person.refresh_from_db()
        self.assertEqual(self.person.graduate_status, "")
        self.assertTrue(send.called)
        self.assertIn("Новый проект", send.call_args[0][1])

    def test_page_empty_when_nobody_waits(self):
        from django.urls import reverse

        self.person.graduate_status = GraduateStatus.PENDING
        self.person.save(update_fields=["graduate_status"])
        response = self.client.get(reverse("interns:waiting"))
        self.assertEqual(response.status_code, 200)


class RockPaperScissorsTests(TestCase):
    """Мини-игра для тех, кто ждёт проект."""

    def test_moves_cover_all_three(self):
        from apps.graduate_bot import bot as botmod

        self.assertEqual(set(botmod.GAME_MOVES.values()), {"rock", "scissors", "paper"})

    def test_every_move_beats_exactly_one(self):
        from apps.graduate_bot import bot as botmod

        self.assertEqual(botmod.GAME_BEATS["rock"], "scissors")
        self.assertEqual(botmod.GAME_BEATS["scissors"], "paper")
        self.assertEqual(botmod.GAME_BEATS["paper"], "rock")


class BotTestUserCommandTests(TestCase):
    """Тестовый выпускник для прогонов бота: заводится и откатывается
    в начало сценария, не трогая настоящих людей."""

    def _run(self, **kwargs):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("bot_test_user", stdout=out, **kwargs)
        return out.getvalue()

    def test_preview_changes_nothing(self):
        text = self._run(phone="0700112233")
        self.assertIn("Предпросмотр", text)
        self.assertFalse(Intern.objects.filter(phone="0700112233").exists())

    def test_creates_person_with_completed_project(self):
        self._run(phone="0700112233", apply=True)
        person = Intern.objects.get(phone="0700112233")
        self.assertEqual(person.graduate_status, GraduateStatus.PENDING)
        membership = TeamMember.objects.get(intern=person)
        self.assertEqual(membership.status, TeamMember.Status.LEFT)
        self.assertEqual(membership.project.status, ProjectStatus.COMPLETED)

    def test_person_is_found_by_the_bot(self):
        self._run(phone="0700112233", apply=True)
        found = services.find_graduates("Тестов Тест")
        self.assertEqual([p.phone for p in found], ["0700112233"])

    def test_reset_returns_person_to_the_start(self):
        self._run(phone="0700112233", apply=True)
        person = Intern.objects.get(phone="0700112233")
        person.telegram_chat_id = 4242
        person.graduate_status = GraduateStatus.WAITING
        person.resume_bank_status = ResumeBankStatus.PENDING
        person.save()
        self._run(phone="0700112233", reset=True, apply=True)
        person.refresh_from_db()
        self.assertIsNone(person.telegram_chat_id)
        self.assertIsNone(person.rules_accepted_at)
        self.assertEqual(person.graduate_status, GraduateStatus.PENDING)
        self.assertEqual(person.resume_bank_status, "")

    def test_phone_required_for_unknown_person(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self._run(name="Нет Такого")


class PhoneFromTelegramTests(TestCase):
    """У части выпускников телефона в базе нет — вход по контакту из
    Telegram: номер приходит от самого Telegram и попадает в карточку."""

    def setUp(self):
        self.person = Intern.objects.create(
            full_name="Фиктивова Эталонгуль", graduate_status=GraduateStatus.PENDING,
        )

    def test_phone_saved_to_card(self):
        services.save_phone_from_telegram(self.person, "+996700112233", 555)
        self.person.refresh_from_db()
        self.assertEqual(self.person.phone, "+996700112233")
        self.assertEqual(self.person.telegram_chat_id, 555)

    def test_head_is_warned(self):
        from apps.notifications.models import Notification

        services.save_phone_from_telegram(self.person, "+996700112233", 555)
        note = Notification.objects.get(dedup_key=f"bot-login-no-phone:{self.person.pk}")
        self.assertIn("Фиктивова Эталонгуль", note.title)
        self.assertIn("+996700112233", note.description)

    def test_written_to_history(self):
        from apps.audit.models import AuditLog

        services.save_phone_from_telegram(self.person, "+996700112233", 555)
        self.assertTrue(
            AuditLog.objects.filter(
                object_id=str(self.person.pk), action="Телефон получен из Telegram",
            ).exists()
        )

    def test_after_saving_person_passes_normal_check(self):
        services.save_phone_from_telegram(self.person, "+996700112233", 555)
        self.person.refresh_from_db()
        self.assertTrue(services.phone_matches(self.person, "0700112233"))


class SharedContactGuardTests(TestCase):
    """Кнопка отдаёт только свой контакт: чужой переслать нельзя."""

    class _Contact:
        def __init__(self, phone_number, user_id):
            self.phone_number = phone_number
            self.user_id = user_id

    class _From:
        def __init__(self, user_id):
            self.id = user_id

    class _Message:
        def __init__(self, contact=None, from_id=1):
            self.contact = contact
            self.from_user = SharedContactGuardTests._From(from_id)

    def test_own_contact_accepted(self):
        from apps.graduate_bot import bot as botmod

        message = self._Message(self._Contact("+996700112233", 1), from_id=1)
        self.assertEqual(botmod._own_contact_phone(message), "+996700112233")

    def test_someone_elses_contact_rejected(self):
        from apps.graduate_bot import bot as botmod

        message = self._Message(self._Contact("+996700112233", 2), from_id=1)
        self.assertIsNone(botmod._own_contact_phone(message))

    def test_plain_text_is_not_a_contact(self):
        from apps.graduate_bot import bot as botmod

        self.assertIsNone(botmod._own_contact_phone(self._Message()))


class NameSearchToleranceTests(TestCase):
    """Имя пишут как придётся: кыргызские буквы заменяют русскими,
    путают отчество, печатают латиницей по инерции."""

    def setUp(self):
        self.project = Project.objects.create(name="Балажан", status=ProjectStatus.COMPLETED)
        self.people = {}
        for name in ("Скетчбекова Күмөрай", "Векторов Лёвхан", "Фиктивова Эталонгуль Кодовна"):
            person = Intern.objects.create(
                full_name=name, graduate_status=GraduateStatus.PENDING,
            )
            TeamMember.objects.create(
                project=self.project, intern=person, role=TeamRole.BACKEND,
                status=TeamMember.Status.LEFT,
            )
            self.people[name] = person

    def _found(self, typed):
        return [p.full_name for p in services.find_graduates(typed)]

    def test_kyrgyz_letters_typed_as_russian(self):
        self.assertIn("Скетчбекова Күмөрай", self._found("Скетчбекова Куморай"))
        self.assertIn("Скетчбекова Күмөрай", self._found("куморай"))

    def test_yo_typed_as_ye(self):
        self.assertIn("Векторов Лёвхан", self._found("Векторов Левхан"))

    def test_wrong_patronymic_still_finds(self):
        """Строгое совпадение не вышло — показываем похожих, а не тупик."""
        self.assertIn(
            "Фиктивова Эталонгуль Кодовна", self._found("Фиктивова Эталонгуль Кодовна"),
        )
        self.assertIn("Фиктивова Эталонгуль Кодовна", self._found("Фиктивова Канает"))

    def test_latin_lookalike_letters(self):
        """Латинские a/o/e/к часто проскакивают вместо кириллических."""
        self.assertIn("Фиктивова Эталонгуль Кодовна", self._found("Фиктивoвa"))

    def test_active_intern_found_among_all_people(self):
        active = Intern.objects.create(full_name="Активный Стажёров")
        self.assertEqual(services.find_graduates("Активный"), [])
        self.assertEqual(
            [p.pk for p in services.find_people("Активный")], [active.pk],
        )

    def test_nobody_matches_returns_empty(self):
        self.assertEqual(self._found("Ноутенко Михаил"), [])
        self.assertEqual(services.find_people("Ноутенко Михаил"), [])


class GraduateRemindersTests(TestCase):
    """Выпускник, не сделавший выбор, и давно ждущий проект не должны
    висеть молча: напоминаем им и показываем сводку руководителю."""

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        self.project = Project.objects.create(
            name="Балажан", status=ProjectStatus.COMPLETED,
        )
        self.long_ago = timezone.localdate() - timedelta(days=30)
        self.yesterday = timezone.localdate() - timedelta(days=1)

    def _graduate(self, name, left_at, status=GraduateStatus.PENDING, chat_id=None):
        person = Intern.objects.create(
            full_name=name, graduate_status=status, telegram_chat_id=chat_id,
        )
        TeamMember.objects.create(
            project=self.project, intern=person, role=TeamRole.BACKEND,
            status=TeamMember.Status.LEFT, left_at=left_at,
        )
        return person

    def _run(self):
        from apps.graduate_bot.tasks import remind_graduates

        with mock.patch("apps.graduate_bot.bot.bot.send_message") as send:
            result = remind_graduates()
        return result, send

    def test_reminds_person_who_has_not_chosen(self):
        person = self._graduate("Завис Выпускников", self.long_ago, chat_id=11)
        result, send = self._run()
        self.assertEqual(result["pending"], 1)
        self.assertTrue(send.called)
        person.refresh_from_db()
        self.assertIsNotNone(person.bot_reminded_at)

    def test_fresh_graduate_is_not_touched(self):
        self._graduate("Только Выпустился", self.yesterday, chat_id=12)
        result, send = self._run()
        self.assertEqual(result["pending"], 0)
        self.assertFalse(send.called)

    def test_waiting_person_reminded_later(self):
        self._graduate(
            "Ждёт Давно", self.long_ago, status=GraduateStatus.WAITING, chat_id=13,
        )
        result, _ = self._run()
        self.assertEqual(result["waiting"], 1)

    def test_person_without_chat_counts_but_gets_no_message(self):
        self._graduate("Без Телеграма", self.long_ago)
        result, send = self._run()
        self.assertEqual(result["pending"], 1)
        self.assertEqual(result["sent"], 0)
        self.assertFalse(send.called)

    def test_second_run_does_not_spam(self):
        self._graduate("Завис Выпускников", self.long_ago, chat_id=11)
        self._run()
        result, send = self._run()
        self.assertEqual(result["sent"], 0)
        self.assertFalse(send.called)

    def test_head_gets_summary(self):
        from apps.notifications.models import Notification

        self._graduate("Завис Выпускников", self.long_ago, chat_id=11)
        self._run()
        self.assertTrue(
            Notification.objects.filter(title__startswith="Выпускники без решения").exists()
        )


class ProjectCloseInvitesGraduatesTests(TestCase):
    """Проект сдан — бот сам зовёт выпускников сделать выбор."""

    def setUp(self):
        self.project = Project.objects.create(name="Балажан")
        self.known = Intern.objects.create(full_name="Знаком Боту", telegram_chat_id=21)
        self.stranger = Intern.objects.create(full_name="Не Писал Боту")
        for person in (self.known, self.stranger):
            TeamMember.objects.create(
                project=self.project, intern=person, role=TeamRole.BACKEND,
                status=TeamMember.Status.ACTIVE,
            )

    def test_invite_goes_only_to_those_who_used_the_bot(self):
        sent = None
        with mock.patch("apps.graduate_bot.bot.bot.send_message") as send:
            sent = services.invite_graduates(
                self.project, [self.known, self.stranger],
            )
        self.assertEqual(sent, 1)
        self.assertEqual(send.call_args[0][0], 21)
        self.assertIn("Балажан", send.call_args[0][1])

    def test_closing_project_invites_graduates(self):
        from apps.projects.models import ProjectStatus
        from apps.projects.services import release_team

        self.project.status = ProjectStatus.COMPLETED
        self.project.save(update_fields=["status"])
        # Telegram — после commit закрытия (UI-03B)
        with mock.patch("apps.graduate_bot.bot.bot.send_message") as send, \
                self.captureOnCommitCallbacks(execute=True):
            release_team(self.project)
        self.assertTrue(send.called)
        self.known.refresh_from_db()
        self.assertEqual(self.known.graduate_status, GraduateStatus.PENDING)

    def test_telegram_failure_does_not_break_closing(self):
        from apps.projects.models import ProjectStatus
        from apps.projects.services import release_team

        self.project.status = ProjectStatus.COMPLETED
        self.project.save(update_fields=["status"])
        with mock.patch("apps.graduate_bot.bot.bot.send_message", side_effect=Exception("boom")), \
                self.captureOnCommitCallbacks(execute=True):
            release_team(self.project)  # не падаем
        self.known.refresh_from_db()
        self.assertEqual(self.known.graduate_status, GraduateStatus.PENDING)


class FinishedInternshipGoesToReserveTests(TestCase):
    """«Закончить стажировку» — человек попадает в резерв кадров и
    перестаёт числиться свободным на проект."""

    def setUp(self):
        self.person = Intern.objects.create(
            full_name="Закончил Стажировку", graduate_status=GraduateStatus.PENDING,
            status=InternStatus.READY,
        )

    def test_card_appears_in_reserve(self):
        from apps.reserve.models import CandidatePool, ReserveCandidate

        services.submit_to_resume_bank(self.person, 555)
        card = ReserveCandidate.objects.get(intern=self.person)
        self.assertEqual(card.full_name, "Закончил Стажировку")
        self.assertEqual(card.pool, CandidatePool.INTERN)

    def test_status_becomes_employable(self):
        services.submit_to_resume_bank(self.person, 555)
        self.person.refresh_from_db()
        self.assertEqual(self.person.status, InternStatus.EMPLOYABLE)

    def test_second_submit_does_not_duplicate_card(self):
        from apps.reserve.models import ReserveCandidate

        services.submit_to_resume_bank(self.person, 555)
        services.submit_to_resume_bank(self.person, 555)
        self.assertEqual(ReserveCandidate.objects.filter(intern=self.person).count(), 1)

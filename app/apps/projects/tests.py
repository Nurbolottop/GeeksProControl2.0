import datetime

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from apps.clients.models import Client
from apps.projects.forms import ProjectForm
from apps.projects.models import (
    DeadlineStatus,
    Project,
    ProjectStage,
    ProjectStageKey,
    ProjectStatus,
    ProjectType,
    lifecycle_stages,
)
from apps.projects import services
from apps.projects.services import calculate_deadline_status, create_project

User = get_user_model()

TODAY = datetime.date(2026, 8, 14)


def make_project(**kwargs) -> Project:
    defaults = {'name': 'Test', 'status': ProjectStatus.ACTIVE, 'progress': 50}
    defaults.update(kwargs)
    return Project(**defaults)


class DeadlineStatusTests(TestCase):
    """Статус срока (ТЗ §8, §22)."""

    def test_completed_project_has_no_deadline_status(self):
        """Проект завершён — контроль срока больше не ведётся (как у
        отменённых/приостановленных): бейдж «Срок» просто пустой."""
        project = make_project(status=ProjectStatus.COMPLETED)
        self.assertEqual(calculate_deadline_status(project, TODAY), '')

    def test_no_deadline_is_on_track(self):
        project = make_project(planned_end_date=None)
        self.assertEqual(
            calculate_deadline_status(project, TODAY), DeadlineStatus.ON_TRACK,
        )

    def test_overdue(self):
        project = make_project(
            planned_end_date=TODAY - datetime.timedelta(days=1),
        )
        self.assertEqual(
            calculate_deadline_status(project, TODAY), DeadlineStatus.OVERDUE,
        )

    def test_behind_when_deadline_close_and_low_progress(self):
        project = make_project(
            planned_end_date=TODAY + datetime.timedelta(days=2), progress=50,
        )
        self.assertEqual(
            calculate_deadline_status(project, TODAY), DeadlineStatus.BEHIND,
        )

    def test_at_risk_when_week_left_and_low_progress(self):
        project = make_project(
            planned_end_date=TODAY + datetime.timedelta(days=6), progress=50,
        )
        self.assertEqual(
            calculate_deadline_status(project, TODAY), DeadlineStatus.AT_RISK,
        )

    def test_on_track_with_good_progress(self):
        project = make_project(
            planned_end_date=TODAY + datetime.timedelta(days=2), progress=95,
        )
        self.assertEqual(
            calculate_deadline_status(project, TODAY), DeadlineStatus.ON_TRACK,
        )

    def test_on_track_far_deadline(self):
        project = make_project(
            planned_end_date=TODAY + datetime.timedelta(days=60), progress=5,
        )
        self.assertEqual(
            calculate_deadline_status(project, TODAY), DeadlineStatus.ON_TRACK,
        )


class CreateProjectTests(TestCase):
    """Набор этапов зависит от типа проекта."""

    def test_web_project_gets_backend_and_frontend(self):
        web_type = ProjectType.objects.create(name='Web-сайт')
        project = make_project(project_type=web_type)
        create_project(project)
        keys = list(project.stages.order_by('order').values_list('key', flat=True))
        self.assertEqual(keys, lifecycle_stages(web_type))
        self.assertIn(ProjectStageKey.BACKEND, keys)
        self.assertIn(ProjectStageKey.FRONTEND, keys)
        self.assertNotIn(ProjectStageKey.MOBILE_DEV, keys)

    def test_mobile_project_gets_mobile_development(self):
        mobile_type = ProjectType.objects.create(name='Mobile App', is_mobile=True)
        project = make_project(project_type=mobile_type)
        create_project(project)
        keys = list(project.stages.values_list('key', flat=True))
        self.assertIn(ProjectStageKey.MOBILE_DEV, keys)
        self.assertNotIn(ProjectStageKey.FRONTEND, keys)

    def test_stage_order_puts_delivery_before_production(self):
        project = make_project()
        create_project(project)
        keys = list(project.stages.order_by('order').values_list('key', flat=True))
        self.assertLess(
            keys.index(ProjectStageKey.DELIVERY),
            keys.index(ProjectStageKey.PRODUCTION),
        )
        self.assertLess(
            keys.index(ProjectStageKey.BACKEND),
            keys.index(ProjectStageKey.STAGING),
        )

    def test_creates_history_and_code(self):
        project = make_project()
        create_project(project)
        self.assertEqual(project.history.count(), 1)
        self.assertTrue(project.code.startswith('GP-'))

    def test_deadline_defaults_to_60_days_from_start(self):
        from django.utils import timezone

        project = make_project()
        create_project(project)
        today = timezone.localdate()
        self.assertEqual(project.start_date, today)
        self.assertEqual(
            project.planned_end_date, today + datetime.timedelta(days=60),
        )

    def test_explicit_start_and_deadline_are_kept(self):
        project = make_project(
            start_date=datetime.date(2026, 1, 1),
            planned_end_date=datetime.date(2026, 3, 1),
        )
        create_project(project)
        self.assertEqual(project.start_date, datetime.date(2026, 1, 1))
        self.assertEqual(project.planned_end_date, datetime.date(2026, 3, 1))

    def test_deadline_computed_from_explicit_start_date(self):
        project = make_project(start_date=datetime.date(2026, 1, 1))
        create_project(project)
        self.assertEqual(
            project.planned_end_date, datetime.date(2026, 1, 1) + datetime.timedelta(days=60),
        )


class ProjectFormTests(TestCase):
    """Перенос deadline требует причину (ТЗ §21)."""

    def _form_data(self, project, **overrides):
        data = {
            'name': project.name,
            'status': project.status,
            'current_stage': project.current_stage,
            'priority': project.priority,
            'progress': project.progress,
            'planned_end_date': project.planned_end_date,
            'change_reason': '',
        }
        data.update(overrides)
        return data

    def test_deadline_change_requires_reason(self):
        project = make_project(planned_end_date=TODAY)
        create_project(project)
        form = ProjectForm(
            self._form_data(
                project,
                planned_end_date=TODAY + datetime.timedelta(days=10),
            ),
            instance=project,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('change_reason', form.errors)

    def test_deadline_change_with_reason_is_valid(self):
        project = make_project(planned_end_date=TODAY)
        create_project(project)
        form = ProjectForm(
            self._form_data(
                project,
                planned_end_date=TODAY + datetime.timedelta(days=10),
                change_reason='Клиент задержал материалы',
            ),
            instance=project,
        )
        self.assertTrue(form.is_valid(), form.errors)


class AuthTests(TestCase):
    """Все внутренние страницы закрыты (ТЗ §2)."""

    def test_anonymous_redirected_to_login(self):
        for url in ['/', '/projects/', '/clients/']:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith('/login/'))

    def test_dashboard_opens_for_authenticated_user(self):
        User.objects.create_user(username='head', password='test12345')
        self.client.login(username='head', password='test12345')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Требует внимания')


class ClientProjectsTests(TestCase):
    def test_client_can_have_multiple_projects(self):
        client_obj = Client.objects.create(organization='Org')
        for index in range(2):
            project = make_project(name=f'P{index}', client=client_obj)
            create_project(project)
        self.assertEqual(client_obj.projects.count(), 2)


class ProjectCreationFlowTests(TestCase):
    """Порядок работы: заказчик заводится вместе с проектом, ПМ — в команде."""

    def setUp(self):
        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)

    def test_new_client_created_from_project_form(self):
        response = self.client.post(reverse("projects:create"), {
            "name": "Туризм", "new_client": "ОсОО Туризм",
            "status": "active", "current_stage": "new",
            "priority": "medium", "progress": 0,
        })
        self.assertEqual(response.status_code, 302)
        client_obj = Client.objects.get(organization="ОсОО Туризм")
        project = Project.objects.get(name="Туризм")
        self.assertEqual(project.client, client_obj)
        # После создания сразу зовём назначать команду
        self.assertTrue(response.url.endswith("?tab=documents&brief=new"))

    def test_existing_client_not_duplicated(self):
        Client.objects.create(organization="ОсОО Омур")
        self.client.post(reverse("projects:create"), {
            "name": "Омур", "new_client": "ОсОО Омур",
            "status": "active", "current_stage": "new",
            "priority": "medium", "progress": 0,
        })
        self.assertEqual(Client.objects.filter(organization="ОсОО Омур").count(), 1)

    def test_pm_and_leads_come_from_team(self):
        from apps.interns.models import Intern
        from apps.teams.models import TeamMember, TeamRole

        project = make_project(name="Омур")
        create_project(project)
        self.assertIsNone(project.pm)
        self.assertEqual(project.leads, [])

        pm = Intern.objects.create(full_name="Каппагуль")
        lead = Intern.objects.create(full_name="Вариантбек")
        TeamMember.objects.create(
            project=project, intern=pm, role=TeamRole.PROJECT_MANAGER, workload=50,
        )
        TeamMember.objects.create(
            project=project, intern=lead, role=TeamRole.TEAM_LEAD, workload=50,
        )
        project.refresh_from_db()
        self.assertEqual(project.pm, pm)
        self.assertEqual(project.leads, [lead])
        self.assertTrue(project.has_pm)

    def test_projects_without_pm_selector(self):
        from apps.projects import selectors

        project = make_project(name="Без ПМ")
        create_project(project)
        self.assertIn(project, selectors.projects_without_pm())

    def test_group_created_together_with_project(self):
        from apps.flows.models import Flow, Group

        Flow.objects.create(number=1, status=Flow.Status.ACTIVE)
        project = make_project(name="Новый")
        create_project(project)
        group = Group.objects.get(project=project)
        self.assertEqual(group.flow.number, 1)
        project.refresh_from_db()
        self.assertEqual(project.flow, group.flow)
        self.assertEqual(project.number_in_flow, group.number)

    def test_flow_created_when_none_exists(self):
        from apps.flows.models import Flow

        self.assertFalse(Flow.objects.exists())
        project = make_project(name="Первый")
        create_project(project)
        self.assertEqual(Flow.objects.count(), 1)
        self.assertIsNotNone(project.group)

    def test_groups_numbered_sequentially(self):
        first = make_project(name="Раз")
        create_project(first)
        second = make_project(name="Два")
        create_project(second)
        self.assertEqual(first.group.number, 1)
        self.assertEqual(second.group.number, 2)

    def test_create_form_has_no_links_and_dates(self):
        from apps.projects.forms import ProjectCreateForm

        fields = set(ProjectCreateForm().fields)
        self.assertNotIn("staging_url", fields)
        self.assertNotIn("planned_end_date", fields)
        self.assertNotIn("progress", fields)

    def test_flow_and_number_assigned_automatically(self):
        """Поток и номер в потоке не спрашиваем — проставляются сами."""
        from apps.flows.models import Flow

        Flow.objects.create(number=7, status=Flow.Status.ACTIVE)
        response = self.client.post(reverse("projects:create"), {"name": "Эпсилонбек"})
        self.assertEqual(response.status_code, 302)
        project = Project.objects.get(name="Эпсилонбек")
        self.assertEqual(project.flow.number, 7)
        self.assertEqual(project.number_in_flow, project.group.number)
        self.assertEqual(project.display_code, f"7.{project.number_in_flow}")

    def test_active_flow_wins_over_older_ones(self):
        from apps.flows.models import Flow

        Flow.objects.create(number=5, status=Flow.Status.FINISHED)
        Flow.objects.create(number=6, status=Flow.Status.ACTIVE)
        self.client.post(reverse("projects:create"), {"name": "Агартуу"})
        self.assertEqual(Project.objects.get(name="Агартуу").flow.number, 6)

    def test_numbers_run_in_sequence_within_the_flow(self):
        self.client.post(reverse("projects:create"), {"name": "Первый"})
        self.client.post(reverse("projects:create"), {"name": "Второй"})
        first = Project.objects.get(name="Первый")
        second = Project.objects.get(name="Второй")
        self.assertEqual(second.flow, first.flow)
        self.assertEqual(second.number_in_flow, first.number_in_flow + 1)

    def test_create_form_fields(self):
        """Ни потока, ни типа: поток считается сам, тип указывает ПМ."""
        from apps.projects.forms import ProjectCreateForm

        self.assertEqual(
            set(ProjectCreateForm().fields),
            {"name", "client", "city", "description"},
        )

    def test_details_panel_does_not_edit_flow_or_type(self):
        from apps.projects.forms import ProjectDetailsForm

        fields = set(ProjectDetailsForm().fields)
        self.assertNotIn("flow", fields)
        self.assertNotIn("project_type", fields)


class ProjectReportTests(TestCase):
    """Отчёт по проекту: одно текстовое поле, дата ставится сама."""

    def setUp(self):
        from apps.projects.models import ProjectReport

        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.project = make_project(name="ОБА")
        create_project(self.project)
        self.model = ProjectReport

    def test_report_created_with_todays_date(self):
        from django.utils import timezone

        response = self.client.post(
            reverse("projects:report_create", args=[self.project.pk]),
            {"text": "Стадия завершения. Исправили баги, впереди презентация."},
        )
        self.assertEqual(response.status_code, 302)
        report = self.model.objects.get(project=self.project)
        self.assertEqual(report.date, timezone.localdate())
        self.assertIn("Стадия завершения", report.text)
        self.assertEqual(report.author, self.user)

    def test_empty_report_not_saved(self):
        self.client.post(
            reverse("projects:report_create", args=[self.project.pk]),
            {"text": "   "},
        )
        self.assertEqual(self.model.objects.count(), 0)

    def test_report_visible_on_tab(self):
        self.model.objects.create(project=self.project, text="Идёт тестирование")
        response = self.client.get(f"{self.project.get_absolute_url()}?tab=report")
        self.assertContains(response, "Идёт тестирование")

    def test_report_updated(self):
        report = self.model.objects.create(project=self.project, text="старое")
        self.client.post(
            reverse("projects:report_update", args=[report.pk]),
            {"text": "новое"},
        )
        report.refresh_from_db()
        self.assertEqual(report.text, "новое")

    def test_report_deleted(self):
        report = self.model.objects.create(project=self.project, text="текст")
        self.client.post(reverse("projects:report_delete", args=[report.pk]))
        self.assertFalse(self.model.objects.filter(pk=report.pk).exists())


class ReportListPageTests(TestCase):
    """Отчёты по всем проектам сразу, с фильтром по проекту (пункт меню
    «Проекты» → «Отчёты»)."""

    def setUp(self):
        from apps.projects.models import ProjectReport

        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.model = ProjectReport
        self.project_a = create_project(make_project(name="Омегагуль"))
        self.project_b = create_project(make_project(name="Псигуль"))

    def test_shows_reports_from_all_projects(self):
        self.model.objects.create(project=self.project_a, text="Отчёт по А")
        self.model.objects.create(project=self.project_b, text="Отчёт по Б")

        response = self.client.get(reverse("projects:reports"))
        self.assertContains(response, "Отчёт по А")
        self.assertContains(response, "Отчёт по Б")

    def test_filter_by_project(self):
        self.model.objects.create(project=self.project_a, text="Отчёт по А")
        self.model.objects.create(project=self.project_b, text="Отчёт по Б")

        response = self.client.get(
            reverse("projects:reports"), {"project": self.project_a.pk},
        )
        self.assertContains(response, "Отчёт по А")
        self.assertNotContains(response, "Отчёт по Б")

    def test_filter_by_text(self):
        self.model.objects.create(project=self.project_a, text="Сдали презентацию")
        self.model.objects.create(project=self.project_b, text="Правим баги")

        response = self.client.get(reverse("projects:reports"), {"q": "презентац"})
        self.assertContains(response, "Сдали презентацию")
        self.assertNotContains(response, "Правим баги")


class LastReportOnOverviewTests(TestCase):
    """Последний отчёт по проекту виден сразу на «Обзоре»."""

    def setUp(self):
        from apps.projects.models import ProjectReport

        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.project = make_project(name="БилимОрдо")
        create_project(self.project)
        self.model = ProjectReport

    def test_no_reports_shows_placeholder(self):
        response = self.client.get(self.project.get_absolute_url())
        self.assertContains(response, "Отчётов по проекту ещё нет")

    def test_latest_report_shown_on_overview(self):
        self.model.objects.create(project=self.project, text="старый отчёт")
        newest = self.model.objects.create(project=self.project, text="новый отчёт")
        response = self.client.get(self.project.get_absolute_url())
        self.assertEqual(response.context["last_report"], newest)
        self.assertContains(response, "новый отчёт")
        self.assertNotContains(response, "старый отчёт")

    def test_overview_links_to_full_report_tab(self):
        self.model.objects.create(project=self.project, text="текст")
        response = self.client.get(self.project.get_absolute_url())
        self.assertContains(response, "?tab=report")




class ProjectListLastReportColumnTests(TestCase):
    """Последний отчёт по каждому проекту цепляется к списку одним запросом.

    Колонки «Отчёт» в таблице больше нет — на её месте кнопка «Графика»,
    но сам отчёт по-прежнему доступен строкам списка.
    """

    def setUp(self):
        from apps.projects.models import ProjectReport

        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.project = make_project(name="Эпсилонбек")
        create_project(self.project)
        self.model = ProjectReport

    def test_graphics_button_shown_for_every_project(self):
        # 2.0: «Графика» — в меню «Ещё» карточки проекта, а не кнопкой в строке списка
        response = self.client.get(self.project.get_absolute_url())
        self.assertContains(response, "?tab=graphics")
        self.assertNotContains(response, "Нет отчёта")

    def test_latest_report_date_shown(self):
        self.model.objects.create(project=self.project, text="старый")
        newest = self.model.objects.create(project=self.project, text="новый отчёт")
        response = self.client.get(reverse("projects:list"))
        found = [
            p for p in response.context["page"].object_list
            if p.pk == self.project.pk
        ][0]
        self.assertEqual(found.last_report, newest)

    def test_last_report_lookup_does_not_scale_per_project(self):
        """Больше проектов с отчётами не должно давать N+1 запросов."""
        from django.db import connection, reset_queries
        from django.test import override_settings

        self.model.objects.create(project=self.project, text="отчёт")

        with override_settings(DEBUG=True):
            reset_queries()
            self.client.get(reverse("projects:list"))
            baseline = len(connection.queries)

            for i in range(10):
                extra = make_project(name=f"Доп {i}")
                create_project(extra)
                self.model.objects.create(project=extra, text=f"отчёт {i}")

            reset_queries()
            self.client.get(reverse("projects:list"))
            with_more_projects = len(connection.queries)

        # N+1 добавил бы примерно по запросу на каждый новый проект (10 лишних);
        # нормальный рост от самих проектов гораздо меньше этого.
        self.assertLess(with_more_projects, baseline + 10)


class StageReopenRollsBackCurrentStageTests(TestCase):
    """Переоткрытие пройденного этапа откатывает project.current_stage.

    Раньше update_stage() (форма «Изменить») никогда не трогал
    current_stage: если уже пройденный этап вручную возвращали в
    работу, бейдж «Этап» на карточке проекта продолжал показывать
    более позднюю стадию, хотя по факту проект туда откатился.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="head", password="x")
        self.project = make_project(name="ОБА")
        create_project(self.project)

    def test_reopening_completed_stage_moves_project_back(self):
        from apps.projects.services import complete_stage, update_stage

        delivery = self.project.stages.get(key=ProjectStageKey.DELIVERY)
        # Все этапы до «Сдачи» уже пройдены — как в реальной жизни,
        # где этапы завершаются по порядку.
        self.project.stages.filter(order__lt=delivery.order).update(
            status=ProjectStage.Status.DONE,
        )

        complete_stage(delivery, user=self.user)
        self.project.refresh_from_db()
        self.assertEqual(self.project.current_stage, ProjectStageKey.PRODUCTION)

        delivery.refresh_from_db()
        delivery.status = delivery.Status.IN_PROGRESS
        update_stage(delivery, user=self.user)

        self.project.refresh_from_db()
        self.assertEqual(self.project.current_stage, ProjectStageKey.DELIVERY)

    def test_editing_a_stage_ahead_of_current_does_not_move_project(self):
        from apps.projects.services import update_stage

        production = self.project.stages.get(key=ProjectStageKey.PRODUCTION)
        production.status = production.Status.IN_PROGRESS
        update_stage(production, user=self.user)

        self.project.refresh_from_db()
        self.assertEqual(self.project.current_stage, ProjectStageKey.NEW)

    def test_completing_a_stage_via_edit_form_advances_project_forward(self):
        """Этап завершили не кнопкой «Завершить», а обычным «Изменить» —
        проект всё равно должен уйти на следующий незавершённый этап."""
        from apps.projects.services import update_stage

        for key in (
            ProjectStageKey.NEW, ProjectStageKey.DOCUMENTS,
            ProjectStageKey.REQUIREMENTS, ProjectStageKey.TEAM_FORMING,
        ):
            stage = self.project.stages.get(key=key)
            stage.status = stage.Status.DONE
            update_stage(stage, user=self.user)

        self.project.refresh_from_db()
        self.assertEqual(self.project.current_stage, ProjectStageKey.DESIGN)


class StageProgressAutoCalcTests(TestCase):
    """Прогресс проекта считается сам — по доле завершённых этапов."""

    def setUp(self):
        self.user = User.objects.create_user(username="head", password="x")
        self.project = make_project(name="Эпсилонбек")
        create_project(self.project)

    def test_new_project_starts_at_zero(self):
        project = make_project(name="Свежий", progress=0)
        create_project(project)
        self.assertEqual(project.progress, 0)

    def test_completing_stages_raises_progress(self):
        from apps.projects.services import update_stage

        for key in (
            ProjectStageKey.NEW, ProjectStageKey.DOCUMENTS,
            ProjectStageKey.REQUIREMENTS, ProjectStageKey.TEAM_FORMING,
        ):
            stage = self.project.stages.get(key=key)
            stage.status = stage.Status.DONE
            update_stage(stage, user=self.user)

        self.project.refresh_from_db()
        # 4 из 12 «настоящих» этапов (без служебного «Завершён» в конце)
        self.assertEqual(self.project.progress, round(100 * 4 / 12))

    def test_completing_project_via_button_reaches_full_progress(self):
        from apps.projects.services import complete_stage

        for stage in self.project.stages.exclude(key=ProjectStageKey.COMPLETED).order_by('order'):
            complete_stage(stage, user=self.user)

        self.project.refresh_from_db()
        self.assertEqual(self.project.progress, 100)

    def test_reopening_a_stage_lowers_progress_back(self):
        from apps.projects.services import complete_stage, update_stage

        stage = self.project.stages.get(key=ProjectStageKey.NEW)
        complete_stage(stage, user=self.user)
        self.project.refresh_from_db()
        self.assertGreater(self.project.progress, 0)

        stage.refresh_from_db()
        stage.status = stage.Status.IN_PROGRESS
        update_stage(stage, user=self.user)

        self.project.refresh_from_db()
        self.assertEqual(self.project.progress, 0)


class ProjectListStageOrderTests(TestCase):
    """Список проектов идёт по этапам: новые сверху, сдача и продакшен внизу."""

    def setUp(self):
        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        for name, stage in [
            ("НаПродакшене", ProjectStageKey.PRODUCTION),
            ("Новичок", ProjectStageKey.NEW),
            ("НаСдаче", ProjectStageKey.DELIVERY),
            ("ВДизайне", ProjectStageKey.DESIGN),
        ]:
            project = make_project(name=name)
            create_project(project)
            project.current_stage = stage
            project.save(update_fields=["current_stage"])

    def test_ordered_by_lifecycle_stage(self):
        response = self.client.get(reverse("projects:list"))
        names = [p.name for p in response.context["page"].object_list]
        self.assertEqual(names, ["Новичок", "ВДизайне", "НаСдаче", "НаПродакшене"])


class ProjectDeleteTests(TestCase):
    """Удаление проекта: только POST, подтверждение кодом, запись в аудит (ТЗ §27)."""

    def setUp(self):
        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.project = make_project(name="Туризм")
        create_project(self.project)
        self.url = reverse("projects:delete", args=[self.project.pk])

    def test_get_does_not_delete(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_wrong_code_does_not_delete(self):
        response = self.client.post(self.url, {"confirm_code": "не тот код"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_delete_with_code_removes_project_and_logs(self):
        from apps.audit.models import AuditLog

        code = self.project.display_code or str(self.project.pk)
        response = self.client.post(self.url, {"confirm_code": code})
        self.assertRedirects(response, reverse("projects:list"))
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())
        entry = AuditLog.objects.get(
            object_type="Project", object_id=str(self.project.pk), action="deleted",
        )
        self.assertEqual(entry.user, self.user)

    def test_cascade_removes_related(self):
        from apps.projects.models import ProjectReport

        self.project.reports.create(text="отчёт", author=self.user)
        code = self.project.display_code or str(self.project.pk)
        self.client.post(self.url, {"confirm_code": code})
        self.assertEqual(ProjectReport.objects.count(), 0)


class FinishedProjectsHiddenFromAllListTests(TestCase):
    """Завершённые и отменённые проекты не выходят в общем списке —
    для них есть страницы «Завершённые» и «Отклонённые»."""

    def setUp(self):
        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.active = make_project(name="Активный")
        create_project(self.active)
        for name, status in [
            ("Завершённый", ProjectStatus.COMPLETED),
            ("Отменённый", ProjectStatus.CANCELLED),
            ("Отказ клиента", ProjectStatus.REFUSED),
        ]:
            project = make_project(name=name, status=status)
            create_project(project)

    def test_all_list_hides_finished_and_cancelled(self):
        response = self.client.get(reverse("projects:list"))
        names = [p.name for p in response.context["page"].object_list]
        self.assertEqual(names, ["Активный"])

    def test_explicit_status_filter_still_works(self):
        response = self.client.get(
            reverse("projects:list"), {"status": ProjectStatus.COMPLETED},
        )
        names = [p.name for p in response.context["page"].object_list]
        self.assertEqual(names, ["Завершённый"])

    def test_dedicated_pages_still_show_them(self):
        response = self.client.get(reverse("projects:list_completed"))
        self.assertContains(response, "Завершённый")
        response = self.client.get(reverse("projects:list_rejected"))
        self.assertContains(response, "Отменённый")
        self.assertContains(response, "Отказ клиента")


class StageBadgeColorTests(TestCase):
    """Бейдж этапа выделяется зелёным, когда проект завершён."""

    def setUp(self):
        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)

    def test_completed_project_shows_green_badge_in_list(self):
        project = make_project(name="Энактус", status=ProjectStatus.COMPLETED)
        create_project(project)
        project.current_stage = ProjectStageKey.COMPLETED
        project.save(update_fields=['current_stage'])

        response = self.client.get(reverse("projects:list_completed"))
        self.assertContains(response, '<span class="gp-badge gp-badge--success">Завершён</span>')

    def test_active_project_keeps_blue_badge_in_list(self):
        project = make_project(name="Эпсилонбек")
        create_project(project)

        response = self.client.get(reverse("projects:list"))
        self.assertContains(response, '<span class="gp-badge gp-badge--primary">Активный</span>')
        self.assertContains(response, 'Новый')

    def test_completed_project_row_is_highlighted(self):
        """Мало одного бейджа — завершённый проект должен быть виден по
        всему ряду в списке, иначе теряется среди активных со схожими
        по цвету бейджами (например, красной просрочкой)."""
        project = make_project(name="Вистайл", status=ProjectStatus.COMPLETED)
        create_project(project)

        response = self.client.get(reverse("projects:list_completed"))
        self.assertContains(response, 'class="gp-row--completed"')

    def test_active_project_row_is_not_highlighted(self):
        project = make_project(name="Эпсилонбек")
        create_project(project)

        response = self.client.get(reverse("projects:list"))
        self.assertNotContains(response, 'gp-row--completed')

    def test_cancelled_project_row_is_highlighted_too(self):
        project = make_project(name="ВФК", status=ProjectStatus.CANCELLED)
        create_project(project)

        response = self.client.get(reverse("projects:list_rejected"))
        self.assertContains(response, 'class="gp-row--cancelled"')

    def test_completed_project_deadline_column_has_no_delay_badge(self):
        """Завершённый проект больше не тянет за собой «Просрочку N дн.» —
        как и у отменённых, «Срок» просто пустой: контроль сроков окончен."""
        project = make_project(
            name="Энактус", status=ProjectStatus.COMPLETED,
            planned_end_date=datetime.date(2026, 5, 6),
            actual_end_date=datetime.date(2026, 8, 24),
        )
        create_project(project)

        response = self.client.get(reverse("projects:list_completed"))
        self.assertNotContains(response, "Просрочка")
        self.assertNotContains(response, "Сдан")


class TeamReleasedOnTerminalStatusTests(TestCase):
    """Команда освобождается, когда проект отменён/завершён — не только
    через кнопку «Завершить проект», но и через обычную форму статуса.

    Раньше это работало только в delivery.complete_project(): если
    статус меняли на «Отменён» (или на «Завершён» вручную, не кнопкой),
    участники команды так и оставались «активными» навсегда — стажёры
    выглядели занятыми на проекте, которого фактически уже нет."""

    def setUp(self):
        self.user = User.objects.create_user(username="head", password="x")
        self.project = make_project(name="ВФК")
        create_project(self.project)

    def _add_member(self):
        from apps.interns.models import Intern
        from apps.teams.models import TeamMember, TeamRole

        intern = Intern.objects.create(full_name="Зетайым")
        return TeamMember.objects.create(
            project=self.project, intern=intern,
            role=TeamRole.BACKEND, status=TeamMember.Status.ACTIVE,
        )

    def test_cancelling_releases_active_members(self):
        from apps.teams.models import TeamMember

        member = self._add_member()
        old_values = {f: getattr(self.project, f) for f in services.TRACKED_FIELDS}
        self.project.status = ProjectStatus.CANCELLED
        services.update_project(self.project, old_values, user=self.user)

        member.refresh_from_db()
        self.assertEqual(member.status, TeamMember.Status.LEFT)
        self.assertIsNotNone(member.left_at)

    def test_refusing_releases_active_members(self):
        from apps.teams.models import TeamMember

        member = self._add_member()
        old_values = {f: getattr(self.project, f) for f in services.TRACKED_FIELDS}
        self.project.status = ProjectStatus.REFUSED
        services.update_project(self.project, old_values, user=self.user)

        member.refresh_from_db()
        self.assertEqual(member.status, TeamMember.Status.LEFT)

    def test_completing_via_edit_form_is_rejected(self):
        """2.0 (UI-03B): «Завершён» ставит только процедура закрытия —
        обычное сохранение формой проект не закрывает и команду не трогает."""
        from django.core.exceptions import ValidationError

        from apps.teams.models import TeamMember

        member = self._add_member()
        old_values = {f: getattr(self.project, f) for f in services.TRACKED_FIELDS}
        self.project.status = ProjectStatus.COMPLETED
        with self.assertRaises(ValidationError):
            services.update_project(self.project, old_values, user=self.user)

        member.refresh_from_db()
        self.assertEqual(member.status, TeamMember.Status.ACTIVE)
        self.project.refresh_from_db()
        self.assertNotEqual(self.project.status, ProjectStatus.COMPLETED)

    def test_pausing_does_not_release_active_members(self):
        """Пауза — не терминальный статус, работа может возобновиться."""
        from apps.teams.models import TeamMember

        member = self._add_member()
        old_values = {f: getattr(self.project, f) for f in services.TRACKED_FIELDS}
        self.project.status = ProjectStatus.PAUSED
        services.update_project(self.project, old_values, user=self.user)

        member.refresh_from_db()
        self.assertEqual(member.status, TeamMember.Status.ACTIVE)

    def test_saving_an_already_cancelled_project_again_is_a_no_op(self):
        """Повторное сохранение без смены статуса не трогает уже вышедших."""
        from apps.teams.models import TeamMember

        member = self._add_member()
        old_values = {f: getattr(self.project, f) for f in services.TRACKED_FIELDS}
        self.project.status = ProjectStatus.CANCELLED
        services.update_project(self.project, old_values, user=self.user)

        left_at = TeamMember.objects.get(pk=member.pk).left_at

        old_values = {f: getattr(self.project, f) for f in services.TRACKED_FIELDS}
        services.update_project(self.project, old_values, user=self.user)

        self.assertEqual(TeamMember.objects.get(pk=member.pk).left_at, left_at)

    def test_cancelling_via_edit_page_releases_team(self):
        """То же самое, но через настоящий POST на страницу редактирования
        проекта — не только напрямую через сервис."""
        from apps.teams.models import TeamMember

        member = self._add_member()
        self.client.force_login(self.user)
        self.client.post(reverse("projects:update", args=[self.project.pk]), {
            "name": self.project.name, "status": ProjectStatus.CANCELLED,
            "current_stage": self.project.current_stage,
            "priority": self.project.priority,
            # Дедлайн теперь подставляется сам при создании — шлём как есть,
            # иначе форма сочтёт это переносом срока и потребует причину.
            "planned_end_date": self.project.planned_end_date,
            "change_reason": "",
        })

        member.refresh_from_db()
        self.assertEqual(member.status, TeamMember.Status.LEFT)


class ProblematicProjectTests(TestCase):
    """Отметка «Проблемный» — вручную, в панели «Детали» на «Обзоре»."""

    def setUp(self):
        self.user = User.objects.create_user(username="head", password="x")
        self.client.force_login(self.user)
        self.project = make_project(name="Эпсилонбек")
        create_project(self.project)

    def test_marking_as_problematic_via_details_panel(self):
        response = self.client.post(
            reverse("projects:section", args=[self.project.pk, "details"]),
            {
                "status": "active", "current_stage": "new", "priority": "medium",
                "is_problematic": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertTrue(self.project.is_problematic)
        self.assertContains(response, "⚠ Проблемный")

    def test_not_problematic_by_default(self):
        # 2.0: статус «Проблемный» не показывается и не используется (UI-02)
        response = self.client.get(self.project.get_absolute_url())
        self.assertNotContains(response, 'Проблемн')


class StagesFollowProjectTypeTests(TestCase):
    """Этап разработки идёт за типом проекта: web → Frontend, мобильный → Mobile."""

    def setUp(self):
        from apps.projects.models import ProjectType
        from apps.projects.services import create_project

        self.user = User.objects.create_user(username="head-type", password="x")
        self.client.force_login(self.user)
        self.web = ProjectType.objects.create(name="Web-сайт")
        self.mobile = ProjectType.objects.create(name="Mobile App", is_mobile=True)
        self.project = make_project(name="Greenbonus", project_type=self.web)
        create_project(self.project)

    def _keys(self):
        return [stage.key for stage in self.project.stages.order_by("order")]

    def test_started_frontend_stage_becomes_mobile(self):
        """Этап уже в работе — переносим его, а не заводим второй."""
        from apps.pm_portal.stages import set_stage_status
        from apps.projects.services import sync_stages_to_type

        for key in ("new", "documents", "requirements", "team_forming", "design", "backend"):
            set_stage_status(self.project, self.project.stages.get(key=key), "done")
        stage = self.project.stages.get(key="frontend")
        set_stage_status(self.project, stage, "in_progress")
        self.project.refresh_from_db()
        self.assertEqual(self.project.current_stage, "frontend")
        self.project.project_type = self.mobile
        self.project.save(update_fields=["project_type"])

        sync_stages_to_type(self.project)
        keys = self._keys()
        self.assertIn("mobile_dev", keys)
        self.assertNotIn("frontend", keys)
        moved = self.project.stages.get(key="mobile_dev")
        self.assertEqual(moved.status, "in_progress")
        self.assertIsNotNone(moved.start_date)
        self.project.refresh_from_db()
        self.assertEqual(self.project.current_stage, "mobile_dev")

    def test_type_change_through_edit_syncs_stages(self):
        from apps.projects import services

        old_values = {f: getattr(self.project, f) for f in services.TRACKED_FIELDS}
        self.project.project_type = self.mobile
        services.update_project(self.project, old_values, user=self.user)
        self.assertIn("mobile_dev", self._keys())
        self.assertNotIn("frontend", self._keys())
        self.assertTrue(self.project.history.filter(field="Тип проекта").exists())

    def test_command_fixes_existing_projects(self):
        from io import StringIO

        from django.core.management import call_command

        Project.objects.filter(pk=self.project.pk).update(project_type=self.mobile)
        out = StringIO()
        call_command("sync_project_stages", stdout=out)
        self.assertIn("Greenbonus", out.getvalue())
        self.assertIn("frontend", self._keys())      # предпросмотр ничего не менял
        call_command("sync_project_stages", apply=True, stdout=StringIO())
        self.assertIn("mobile_dev", self._keys())


class GraduateOnlyWhenFreeTests(TestCase):
    """Завершение одного проекта не делает выпускником того, кто занят
    на других: у людей бывает два-три проекта сразу."""

    def setUp(self):
        from apps.projects.services import create_project

        self.user = User.objects.create_user(username="head-grad", password="x")
        self.client.force_login(self.user)
        self.done = create_project(Project(name="Закрываемый"))
        self.live = create_project(Project(name="Идущий"))

    def _member(self, intern, project):
        from apps.teams.models import TeamMember, TeamRole

        return TeamMember.objects.create(
            project=project, intern=intern, role=TeamRole.BACKEND,
            status=TeamMember.Status.ACTIVE,
        )

    def test_person_on_another_live_project_is_not_a_graduate(self):
        from apps.interns.models import Intern
        from apps.projects.models import ProjectStatus
        from apps.projects.services import release_team

        busy = Intern.objects.create(full_name="Ещё Работает")
        self._member(busy, self.done)
        self._member(busy, self.live)

        self.done.status = ProjectStatus.COMPLETED
        self.done.save(update_fields=["status"])
        release_team(self.done)

        busy.refresh_from_db()
        self.assertEqual(busy.graduate_status, "")

    def test_person_with_nothing_left_becomes_a_graduate(self):
        from apps.interns.models import GraduateStatus, Intern
        from apps.projects.models import ProjectStatus
        from apps.projects.services import release_team

        free = Intern.objects.create(full_name="Освободился")
        self._member(free, self.done)

        self.done.status = ProjectStatus.COMPLETED
        self.done.save(update_fields=["status"])
        release_team(self.done)

        free.refresh_from_db()
        self.assertEqual(free.graduate_status, GraduateStatus.PENDING)

    def test_removing_one_by_one_follows_the_same_rule(self):
        from apps.interns.models import GraduateStatus, Intern
        from apps.teams.models import TeamMember
        from apps.teams.services import leave_team

        busy = Intern.objects.create(full_name="Снятый Но Занятый")
        member = self._member(busy, self.done)
        self._member(busy, self.live)
        leave_team(member, reason=TeamMember.LeftReason.PROJECT_ENDED)
        busy.refresh_from_db()
        self.assertEqual(busy.graduate_status, "")

        alone = Intern.objects.create(full_name="Снятый И Свободный")
        member = self._member(alone, self.done)
        leave_team(member, reason=TeamMember.LeftReason.PROJECT_ENDED)
        alone.refresh_from_db()
        self.assertEqual(alone.graduate_status, GraduateStatus.PENDING)

    def test_finished_project_does_not_count_as_live(self):
        """Второй проект тоже завершён — значит человек всё-таки выпускник."""
        from apps.interns.models import GraduateStatus, Intern
        from apps.projects.models import ProjectStatus
        from apps.projects.services import release_team

        person = Intern.objects.create(full_name="Оба Закрылись")
        self._member(person, self.done)
        self._member(person, self.live)
        self.live.status = ProjectStatus.COMPLETED
        self.live.save(update_fields=["status"])

        self.done.status = ProjectStatus.COMPLETED
        self.done.save(update_fields=["status"])
        release_team(self.done)

        person.refresh_from_db()
        self.assertEqual(person.graduate_status, GraduateStatus.PENDING)


class BriefLinkOnProjectCreateTests(TestCase):
    """Создали проект — ссылка на бриф уже готова, её не надо искать."""

    def setUp(self):
        self.user = User.objects.create_user(username="head-brief", password="x")
        self.client.force_login(self.user)

    def _create(self, name="Новый проект"):
        response = self.client.post(reverse("projects:create"), {"name": name})
        return response, Project.objects.get(name=name)

    def test_link_is_issued_with_the_project(self):
        from apps.documents.services import active_brief_link

        _, project = self._create()
        link = active_brief_link(project)
        self.assertIsNotNone(link)
        self.assertTrue(link.is_open)

    def test_lands_on_documents_with_the_link_highlighted(self):
        response, project = self._create()
        self.assertRedirects(
            response, f"{project.get_absolute_url()}?tab=documents&brief=new",
        )

    def test_message_carries_the_url(self):
        from apps.documents.services import active_brief_link

        response, project = self._create()
        link = active_brief_link(project)
        text = ' '.join(str(m) for m in get_messages(response.wsgi_request))
        self.assertIn(link.get_absolute_url(), text)

    def test_page_offers_to_copy_it(self):
        from apps.documents.services import active_brief_link

        _, project = self._create()
        link = active_brief_link(project)
        page = self.client.get(f"{project.get_absolute_url()}?tab=documents&brief=new")
        self.assertContains(page, link.get_absolute_url())
        self.assertContains(page, "data-gp-copy=\"brief-link-url\"")
        self.assertContains(page, "gp-copyline--fresh")

    def test_second_project_gets_its_own_link(self):
        from apps.documents.services import active_brief_link

        _, first = self._create("Первый")
        _, second = self._create("Второй")
        self.assertNotEqual(
            active_brief_link(first).token, active_brief_link(second).token,
        )

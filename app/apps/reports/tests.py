import datetime

from django.test import TestCase
from django.utils import timezone

from apps.projects.models import Project, ProjectStatus
from apps.reports import weekly_form
from apps.reports.services import (
    calculate_kpi,
    generate_weekly_report,
    weekly_metrics,
    week_bounds,
)


class WeeklyReportTests(TestCase):
    """Недельный отчёт (ТЗ §24.1)."""

    def test_week_bounds(self):
        # 14.08.2026 — пятница; неделя начинается в понедельник 10.08
        start, end = week_bounds(datetime.date(2026, 8, 14))
        self.assertEqual(start, datetime.date(2026, 8, 10))
        self.assertEqual(end, datetime.date(2026, 8, 16))

    def test_generate_is_idempotent(self):
        report1 = generate_weekly_report()
        report2 = generate_weekly_report()
        self.assertEqual(report1.pk, report2.pk)

    def test_metrics_count_active_projects(self):
        Project.objects.create(name='A')
        start, _ = week_bounds(timezone.localdate())
        metrics = weekly_metrics(start)
        self.assertEqual(metrics['active_projects'], 1)


class WeeklyFormTests(TestCase):
    """Недельный отчёт по форме руководителя: период — Пн–Вс."""

    def test_week_bounds_from_any_weekday(self):
        start, end = weekly_form.week_bounds(datetime.date(2026, 8, 19))
        self.assertEqual(start, datetime.date(2026, 8, 17))
        self.assertEqual(end, datetime.date(2026, 8, 23))

    def test_only_this_week_contracts_counted(self):
        monday = datetime.date(2026, 8, 17)
        Project.objects.create(name='На неделе', contract_date=monday)
        Project.objects.create(
            name='Раньше', contract_date=monday - datetime.timedelta(days=3),
        )
        data = weekly_form.build(monday)
        self.assertEqual(data['projects']['signed_contracts'], 1)

    def test_new_projects_counted_by_creation_date(self):
        """«Новые проекты за неделю» — по дате создания записи в системе,
        отдельно от «Подписано договоров» (та — по дате договора)."""
        monday = datetime.date(2026, 8, 17)
        created_this_week = Project.objects.create(name='Новый')
        created_this_week.created_at = timezone.make_aware(
            datetime.datetime(2026, 8, 18, 10, 0),
        )
        created_this_week.save(update_fields=['created_at'])

        created_last_week = Project.objects.create(name='Старый')
        created_last_week.created_at = timezone.make_aware(
            datetime.datetime(2026, 8, 10, 10, 0),
        )
        created_last_week.save(update_fields=['created_at'])

        data = weekly_form.build(monday)
        self.assertEqual(data['projects']['new'], 1)

    def test_problematic_projects_counted(self):
        """Проблемный проект отмечается вручную (галочка в «Деталях»),
        отчёт просто считает текущее количество отмеченных."""
        Project.objects.create(name='Горит', is_problematic=True)
        Project.objects.create(name='Норм', is_problematic=False)
        data = weekly_form.build(datetime.date(2026, 8, 17))
        self.assertEqual(data['stopped']['problematic'], 1)

    def test_sections_cover_all_blocks(self):
        data = weekly_form.build(datetime.date(2026, 8, 17))
        sections = weekly_form.as_sections(data)
        titles = [section['title'] for section in sections]
        self.assertIn('Проекты в разработке', titles)
        self.assertIn('Внутренние собрания за неделю', titles)
        self.assertEqual(len(sections), len(weekly_form.SECTIONS))

    def test_missing_source_data_marked_as_no_data(self):
        """Пустое поле в базе — это прочерк с подсказкой, а не ноль."""
        data = weekly_form.build(datetime.date(2026, 8, 17))
        rows = {
            row['label']: row
            for section in weekly_form.as_sections(data)
            for row in section['rows']
        }
        new_interns = rows['Вышли на стажировку за неделю']
        self.assertTrue(new_interns['no_data'])
        self.assertIn('дата начала стажировки', new_interns['hint'])

    def test_real_zero_stays_zero_when_data_exists(self):
        Project.objects.create(
            name='С договором', contract_date=datetime.date(2026, 7, 1),
        )
        data = weekly_form.build(datetime.date(2026, 8, 17))
        rows = {
            row['label']: row
            for section in weekly_form.as_sections(data)
            for row in section['rows']
        }
        signed = rows['Подписано договоров за неделю']
        self.assertFalse(signed['no_data'])
        self.assertEqual(signed['value'], 0)

    def test_cancelled_counted_by_status_change_in_week(self):
        from apps.projects.models import ProjectStatusHistory, ProjectStatus

        project = Project.objects.create(name='Стоп')
        record = ProjectStatusHistory.objects.create(
            project=project, field='Статус',
            new_value=ProjectStatus.CANCELLED.label,
        )
        ProjectStatusHistory.objects.filter(pk=record.pk).update(
            created_at=timezone.make_aware(
                datetime.datetime(2026, 8, 18, 12, 0),
            ),
        )
        data = weekly_form.build(datetime.date(2026, 8, 17))
        self.assertEqual(data['stopped']['by_us'], 1)
        # прошлая неделя — событие туда не попадает
        older = weekly_form.build(datetime.date(2026, 8, 10))
        self.assertEqual(older['stopped']['by_us'], 0)


class KPITests(TestCase):
    """KPI руководителя (ТЗ §25)."""

    def test_on_time_delivery_rate(self):
        today = timezone.localdate()
        # Сдан в срок
        Project.objects.create(
            name='OnTime', status=ProjectStatus.COMPLETED,
            planned_end_date=today, actual_end_date=today,
        )
        # Сдан с задержкой 4 дня
        Project.objects.create(
            name='Late', status=ProjectStatus.COMPLETED,
            planned_end_date=today - datetime.timedelta(days=4),
            actual_end_date=today,
        )
        kpi = calculate_kpi()
        self.assertEqual(kpi['on_time_delivery_rate'], 50)
        self.assertEqual(kpi['average_delay_days'], 4)
        self.assertEqual(kpi['completed_projects'], 2)

    def test_kpi_with_no_projects(self):
        kpi = calculate_kpi()
        self.assertIsNone(kpi['on_time_delivery_rate'])
        self.assertEqual(kpi['overdue_projects'], 0)


class ErrorPagesTests(TestCase):
    """Несуществующая страница должна отдавать 404, а не 500."""

    def test_missing_page_renders_404(self):
        from django.template.loader import render_to_string
        from django.test import RequestFactory
        from django.contrib.auth.models import AnonymousUser

        request = RequestFactory().get("/no-such-page/")
        request.user = AnonymousUser()
        html = render_to_string("404.html", request=request)
        self.assertIn("Страница не найдена", html)



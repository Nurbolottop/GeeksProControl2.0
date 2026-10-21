"""Еженедельный отчёт и снимки KPI."""
from celery import shared_task


@shared_task
def generate_weekly_metrics() -> str:
    from apps.reports.services import generate_weekly_report, snapshot_kpi
    report = generate_weekly_report()
    snapshot_kpi('week')
    return str(report)


@shared_task
def generate_monthly_kpi_snapshot() -> str:
    from apps.reports.services import snapshot_kpi
    return str(snapshot_kpi('month'))

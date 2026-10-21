from django.contrib import admin

from apps.reports.models import KPISnapshot, WeeklyReport


@admin.register(WeeklyReport)
class WeeklyReportAdmin(admin.ModelAdmin):
    list_display = ('week_start', 'created_at')


@admin.register(KPISnapshot)
class KPISnapshotAdmin(admin.ModelAdmin):
    list_display = ('period_type', 'period_start', 'created_at')
    list_filter = ('period_type',)

from django.contrib import admin

from apps.risks.models import DelayReason, Risk


@admin.register(DelayReason)
class DelayReasonAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active')
    list_editable = ('is_active',)


@admin.register(Risk)
class RiskAdmin(admin.ModelAdmin):
    list_display = ('project', 'category', 'status', 'is_auto', 'created_at')
    list_filter = ('category', 'status', 'is_auto')

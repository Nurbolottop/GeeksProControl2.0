from django.contrib import admin

from apps.flows.models import Flow


@admin.register(Flow)
class FlowAdmin(admin.ModelAdmin):
    list_display = ('number', 'status', 'start_date', 'end_date')
    list_filter = ('status',)

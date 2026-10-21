from django.contrib import admin

from apps.notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('title', 'level', 'is_read', 'is_closed', 'created_at')
    list_filter = ('level', 'is_read', 'is_closed')
    search_fields = ('title',)

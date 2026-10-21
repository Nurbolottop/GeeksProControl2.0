from django.contrib import admin

from apps.scripts.models import Script


@admin.register(Script)
class ScriptAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'is_pinned', 'created_by', 'updated_at')
    list_filter = ('category', 'is_pinned')
    search_fields = ('title', 'body', 'category')

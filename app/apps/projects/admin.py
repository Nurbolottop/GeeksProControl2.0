from django import forms
from django.contrib import admin

from apps.projects.forms import NoManualCompletionMixin
from apps.projects.models import (
    Project,
    ProjectAccess,
    ProjectLink,
    ProjectStage,
    ProjectStatusHistory,
    ProjectType,
)


class ProjectAccessInline(admin.TabularInline):
    model = ProjectAccess
    extra = 0


@admin.register(ProjectType)
class ProjectTypeAdmin(admin.ModelAdmin):
    list_display = ('name',)


class ProjectStageInline(admin.TabularInline):
    model = ProjectStage
    extra = 0


class ProjectLinkInline(admin.TabularInline):
    model = ProjectLink
    extra = 0


class ProjectAdminForm(NoManualCompletionMixin, forms.ModelForm):
    class Meta:
        model = Project
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._limit_status()


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    form = ProjectAdminForm
    list_display = (
        'code', 'name', 'client', 'status', 'current_stage',
        'priority', 'progress', 'planned_end_date', 'is_archived',
    )
    list_filter = ('status', 'current_stage', 'priority', 'is_archived')
    search_fields = ('code', 'name', 'client__organization')
    inlines = [ProjectStageInline, ProjectLinkInline, ProjectAccessInline]


@admin.register(ProjectStatusHistory)
class ProjectStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ('project', 'field', 'old_value', 'new_value', 'user', 'created_at')
    list_filter = ('field',)
    readonly_fields = (
        'project', 'field', 'old_value', 'new_value', 'reason', 'user',
    )

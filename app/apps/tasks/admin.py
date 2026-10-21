from django.contrib import admin

from apps.tasks.models import Task, TaskAttachment, TaskComment, TaskTemplate


class TaskCommentInline(admin.TabularInline):
    model = TaskComment
    extra = 0


class TaskAttachmentInline(admin.TabularInline):
    model = TaskAttachment
    extra = 0


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'project', 'assignee', 'status', 'priority',
        'deadline', 'is_archived',
    )
    list_filter = ('status', 'priority', 'is_archived')
    search_fields = ('title', 'project__name')
    inlines = [TaskCommentInline, TaskAttachmentInline]


@admin.register(TaskTemplate)
class TaskTemplateAdmin(admin.ModelAdmin):
    list_display = ('kind', 'order', 'title', 'is_active')
    list_filter = ('kind', 'is_active')
    list_editable = ('order', 'is_active')

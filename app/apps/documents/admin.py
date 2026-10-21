from django.contrib import admin

from apps.documents.models import (
    Document, DocumentTemplate, DocumentType, ProjectBrief, ProjectBriefLink,
)


@admin.register(DocumentType)
class DocumentTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'required_for_delivery')
    list_editable = ('required_for_delivery',)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        'project', 'doc_type', 'number', 'status',
        'is_signed', 'signed_date', 'is_archived',
    )
    list_filter = ('doc_type', 'status', 'is_signed', 'is_archived')
    search_fields = ('number', 'project__name')


@admin.register(DocumentTemplate)
class DocumentTemplateAdmin(admin.ModelAdmin):
    list_display = ('doc_type', 'name', 'uploaded_by', 'created_at')
    list_filter = ('doc_type',)
    search_fields = ('name',)


@admin.register(ProjectBriefLink)
class ProjectBriefLinkAdmin(admin.ModelAdmin):
    list_display = ('project', 'is_active', 'submissions', 'used_at', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('project__name', 'token')


@admin.register(ProjectBrief)
class ProjectBriefAdmin(admin.ModelAdmin):
    list_display = ('project', 'submitted_at', 'created_at')
    search_fields = ('project__name',)

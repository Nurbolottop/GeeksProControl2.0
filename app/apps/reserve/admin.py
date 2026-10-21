from django.contrib import admin

from apps.reserve.models import (
    ReserveCandidate, ReserveEvent, ReserveInvite, ReserveRecommendation,
    ReserveShareLink,
)


class ReserveRecommendationInline(admin.TabularInline):
    model = ReserveRecommendation
    extra = 0


@admin.register(ReserveCandidate)
class ReserveCandidateAdmin(admin.ModelAdmin):
    list_display = (
        'full_name', 'specialization', 'geekspro_level', 'rating',
        'status', 'city', 'is_looking_for_job', 'is_archived',
    )
    list_filter = ('status', 'geekspro_level', 'specialization', 'is_looking_for_job')
    search_fields = ('full_name', 'phone', 'email', 'skills')
    readonly_fields = ('rating', 'submitted_at', 'consent_at', 'status_changed_at')
    inlines = [ReserveRecommendationInline]


@admin.register(ReserveInvite)
class ReserveInviteAdmin(admin.ModelAdmin):
    list_display = (
        'token', 'recipient', 'is_active', 'expires_at', 'submissions', 'used_at',
    )
    list_filter = ('is_active',)
    readonly_fields = ('token', 'submissions', 'used_at')


@admin.register(ReserveEvent)
class ReserveEventAdmin(admin.ModelAdmin):
    list_display = ('candidate', 'kind', 'title', 'user', 'created_at')
    list_filter = ('kind',)
    search_fields = ('candidate__full_name', 'title')


@admin.register(ReserveShareLink)
class ReserveShareLinkAdmin(admin.ModelAdmin):
    list_display = (
        'candidate', 'recipient', 'show_contacts', 'is_active', 'expires_at',
        'views', 'viewed_at',
    )
    list_filter = ('is_active', 'show_contacts')
    search_fields = ('candidate__full_name', 'recipient', 'token')
    raw_id_fields = ('candidate', 'candidates')

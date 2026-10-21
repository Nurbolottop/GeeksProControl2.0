from django.contrib import admin

from apps.teams.models import TeamMember


@admin.register(TeamMember)
class TeamMemberAdmin(admin.ModelAdmin):
    list_display = (
        'project', 'person_name', 'role', 'workload', 'status', 'joined_at',
    )
    list_filter = ('role', 'status')
    search_fields = ('project__name', 'user__username', 'intern__full_name')

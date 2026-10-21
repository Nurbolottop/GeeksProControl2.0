from django.shortcuts import get_object_or_404

from apps.projects.models import Project
from apps.teams.models import TeamMember, TeamRole


def pm_project_or_404(user, project_pk) -> Project:
    """Единственная точка проверки: проект, где user — активный ПМ.

    ПМ сегодня — это TeamMember с role='pm', привязанный к проекту через
    intern (не через свой user напрямую) — сам логин лежит в intern.user.
    """
    return get_object_or_404(
        Project,
        pk=project_pk,
        team_members__intern__user=user,
        team_members__role=TeamRole.PROJECT_MANAGER,
        team_members__status=TeamMember.Status.ACTIVE,
    )


def pm_projects(user):
    """Проекты, где user — активный ПМ (для дашборда)."""
    return Project.objects.filter(
        team_members__intern__user=user,
        team_members__role=TeamRole.PROJECT_MANAGER,
        team_members__status=TeamMember.Status.ACTIVE,
    ).distinct()

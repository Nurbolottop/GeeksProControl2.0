from django.db.models import Q
from django.shortcuts import get_object_or_404

from apps.projects.models import Project, ProjectStatus
from apps.teams.models import TeamMember, TeamRole


# Сданные и закрытые проекты: команду там распускают, поэтому тимлид
# числится в них уже как вышедший — но свои проекты он должен видеть.
FINISHED_STATUSES = (
    ProjectStatus.COMPLETED, ProjectStatus.CANCELLED, ProjectStatus.REFUSED,
)


def _own_projects(user):
    """Проекты тимлида: где он в команде сейчас или был к моменту сдачи.

    Условия стоят в одном filter() — значит, относятся к одной и той же
    записи в команде: снятый с активного проекта тимлид доступ теряет, а
    сданный проект остаётся у того, кто его вёл.
    """
    return Project.objects.filter(
        Q(team_members__status=TeamMember.Status.ACTIVE)
        | Q(team_members__status=TeamMember.Status.LEFT, status__in=FINISHED_STATUSES),
        team_members__intern__user=user,
        team_members__role=TeamRole.TEAM_LEAD,
    )


def lead_project_or_404(user, project_pk) -> Project:
    """Единственная точка проверки: проект, который ведёт или вёл user.

    Логин тимлида, как и у ПМ, лежит в intern.user — сам тимлид на
    проекте это TeamMember с role='team_lead', привязанный через intern.
    """
    return get_object_or_404(_own_projects(user).distinct(), pk=project_pk)


def active_lead_project_or_404(user, project_pk) -> Project:
    """Проект, где user — активный тимлид: для любых изменений (UI-03C).

    lead_project_or_404 открывает и сданные проекты, которые тимлид вёл, —
    только для просмотра истории; менять там он ничего не может.
    """
    return get_object_or_404(
        Project.objects.filter(
            team_members__intern__user=user, team_members__role=TeamRole.TEAM_LEAD,
            team_members__status=TeamMember.Status.ACTIVE,
        ).distinct(),
        pk=project_pk,
    )


def lead_projects(user):
    """Проекты тимлида для дашборда — вместе со сданными и закрытыми."""
    return _own_projects(user).distinct()


def lead_own_role(user):
    """Направление тимлида по его собственной специализации (Backend,
    Frontend...) — именно этих стажёров команды он видит и добавляет,
    остальные направления и ПМ в его портале не показываются."""
    from apps.teams.forms import ROLE_BY_SPECIALIZATION

    intern = getattr(user, 'intern_profile', None)
    spec = getattr(intern, 'specialization', None) if intern else None
    return ROLE_BY_SPECIALIZATION.get(spec.name) if spec else None


def pending_meeting(project, user):
    """Последнее собрание проекта, которое тимлид ещё не закрыл — не
    всем его стажёрам своего направления отмечена посещаемость и
    выставлена оценка. None, если группы/собраний нет или всё закрыто.

    Отмечать и оценивать обязательно: новое собрание нельзя создать,
    пока не закрыто предыдущее (см. meeting_create).
    """
    from apps.attendance import services as attendance_services
    from apps.attendance.models import MeetingKind

    group = getattr(project, 'group', None)
    if group is None:
        return None
    own_role = lead_own_role(user)
    meetings = group.meetings.filter(kind=MeetingKind.INTERNAL)
    if own_role:
        # Чужое собрание тимлида не касается: закрывать нужно только свои
        meetings = meetings.filter(direction__in=['', own_role])
    meeting = meetings.order_by('-date').first()
    if meeting is None:
        return None
    members = attendance_services.attendance_eligible_members(group).filter(
        status=TeamMember.Status.ACTIVE, intern__isnull=False,
    )
    if own_role:
        members = members.filter(role=own_role)
    completion = attendance_services.meeting_completion(meeting, members)
    return None if completion['complete'] else meeting

"""Управление ПМ и тимлидами (этап 6A). Все операции — только руководитель,
атомарно, с историей; доступ даёт проектное назначение, а не роль в User.

Различаем четыре вещи:
* направление стажировки — Intern.specialization (PM — тоже направление);
* статус стажёра / выпускника — Intern.status, Internship, graduation;
* управленческие полномочия — активное TeamMember(role=pm / team_lead) в
  конкретном проекте (порталы проверяют именно его: pm_project_or_404,
  active_lead_project_or_404);
* кадровый статус — Intern.staff_status / staff_role.

Снятие с проекта закрывает назначение (запись остаётся), и права на этот
проект пропадают сразу — порталы смотрят только активные назначения.
Прекращение работы закрывает все назначения и деактивирует учётку: старая
сессия перестаёт работать (ModelBackend не пускает неактивных).
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.interns.models import Intern, Internship, InternStatus, Position, StaffRole, StaffStatus
from apps.teams.models import TeamMember, TeamRole


class StaffError(ValidationError):
    pass


def _require_head(user):
    from apps.accounts.access import has_full_access

    if not has_full_access(user):
        raise PermissionDenied('Назначениями ПМ и тимлидов управляет руководитель.')


def _log(obj, action, user, **kwargs):
    from apps.audit.services import log as audit_log

    audit_log(obj, action, user=user, **kwargs)


def _lock(intern):
    return Intern.objects.select_for_update().get(pk=intern.pk)


def _require_open_project(project):
    """Завершённый, отменённый или отказной проект новых ПМ и тимлидов не получает
    (этап 10A: раньше правило набора касалось только стажёров направлений)."""
    from apps.projects.models import Project, ProjectStatus

    status = Project.objects.filter(pk=project.pk).values_list('status', flat=True).first()
    if status not in (ProjectStatus.ACTIVE, ProjectStatus.PAUSED):
        raise StaffError(f'Проект «{project.name}» закрыт — новых назначений нет.')


def _end(member, reason):
    member.status = TeamMember.Status.LEFT
    member.left_at = timezone.localdate()
    member.left_reason = reason
    member.save(update_fields=['status', 'left_at', 'left_reason', 'updated_at'])
    if member.intern_id and member.project_id:
        from apps.tasks.workflow import release_stale

        release_stale(intern_ids=[member.intern_id], project_ids=[member.project_id],
                      reason=member.get_left_reason_display().lower())


def _open_assignment(project, intern, role):
    """Активное назначение (повторное — не создаёт дубль)."""
    existing = TeamMember.objects.filter(project=project, intern=intern, role=role,
                                         status=TeamMember.Status.ACTIVE).first()
    if existing:
        return existing, False
    member = TeamMember(project=project, intern=intern, role=role, status=TeamMember.Status.ACTIVE,
                        group=getattr(project, 'group', None), joined_at=timezone.localdate())
    return member, True


# ------------------------------------------------------------------- ПМ

def assign_pm(project, intern, user):
    """Назначить ПМ проекта. Стажёр направления PM подчиняется правилам
    состава (save_membership); ПМ-сотрудник — нет."""
    from apps.teams.services import save_membership

    _require_head(user)
    if intern.is_archived or intern.staff_status in (StaffStatus.DISMISSED, StaffStatus.STUDIO):
        raise StaffError(f'{intern.full_name} у нас больше не работает.')
    _require_open_project(project)
    with transaction.atomic():
        _lock(intern)
        member, made = _open_assignment(project, intern, TeamRole.PROJECT_MANAGER)
        if not made:
            return member, False
        # было участие в другой роли на этом проекте — становится ПМ
        other = TeamMember.objects.filter(project=project, intern=intern, status=TeamMember.Status.ACTIVE).first()
        if other:
            member = other
            member.role = TeamRole.PROJECT_MANAGER
        error = save_membership(member)
        if error:
            raise StaffError(error)
        _log(project, 'ПМ назначен', user, new_value=intern.full_name)
    transaction.on_commit(lambda: _tell_staff(intern, f'Вы назначены ПМ проекта «{project.name}».',
                                              f'pm-assigned:{member.pk}'))
    return member, True


def remove_assignment(member, user, reason=TeamMember.LeftReason.OTHER):
    """Снять ПМ или тимлида с проекта: назначение закрыто, история цела."""
    _require_head(user)
    with transaction.atomic():
        member = TeamMember.objects.select_for_update().get(pk=member.pk)
        if member.status != TeamMember.Status.ACTIVE:
            raise StaffError('Назначение уже закрыто.')
        _end(member, reason)
        _log(member.project, f'Снят(а) с проекта: {member.get_role_display()}', user,
             new_value=member.person_name)
    if member.intern_id:
        transaction.on_commit(lambda: _tell_staff(member.intern, f'Вы сняты с проекта «{member.project.name}» '
                                                                 f'({member.get_role_display()}).',
                                                  f'staff-removed:{member.pk}'))
    if member.role == TeamRole.TEAM_LEAD:
        notify_uncovered([member.project_id])
    return member


def replace(member, new_intern, user):
    """Заменить ПМ / тимлида на проекте: старое назначение — «Заменён(а)»."""
    _require_head(user)
    if new_intern.pk == member.intern_id:
        raise StaffError('Это тот же человек.')
    with transaction.atomic():
        old = TeamMember.objects.select_for_update().get(pk=member.pk)
        if old.status != TeamMember.Status.ACTIVE:
            raise StaffError('Назначение уже закрыто — замена не нужна.')
        _end(old, TeamMember.LeftReason.REPLACED)
        if old.role == TeamRole.PROJECT_MANAGER:
            new, _made = assign_pm(old.project, new_intern, user)
        else:
            new, _made = assign_lead(old.project, new_intern, user, direction=lead_member_direction(old))
        _log(old.project, f'Замена: {old.get_role_display()}', user, old_value=old.person_name,
             new_value=new_intern.full_name)
    return new


# -------------------------------------------------------------- тимлиды

def own_direction(intern) -> str:
    from apps.teams.forms import ROLE_BY_SPECIALIZATION

    return ROLE_BY_SPECIALIZATION.get(intern.specialization.name if intern and intern.specialization_id else '', '')


def lead_member_direction(member) -> str:
    """Направление, которое ведёт назначение тимлида (8B): явное или по специализации."""
    return member.lead_direction or own_direction(member.intern)


def is_staff_lead(intern) -> bool:
    return intern.position == Position.TEAM_LEAD and intern.staff_status != StaffStatus.DISMISSED \
        and intern.staff_status != StaffStatus.STUDIO


def assign_lead(project, intern, user, direction=None):
    """Назначить тимлида (сотрудника) на проект и направление. По умолчанию —
    направление по его специализации; другое направление — отдельное
    назначение (тимлид двух направлений — две обязанности, этап 8B).

    Действующего стажёра сначала повышают (promote_to_lead) — это отдельное
    явное решение руководителя."""
    from apps.teams.rules import DIRECTION_ROLES

    _require_head(user)
    if not is_staff_lead(intern):
        raise StaffError(f'{intern.full_name} — не тимлид-сотрудник. Сначала повысьте до тимлида.')
    own = own_direction(intern)
    direction = direction or own
    if direction and direction not in DIRECTION_ROLES:
        raise StaffError('Неизвестное направление.')
    _require_open_project(project)
    with transaction.atomic():
        _lock(intern)
        active = TeamMember.objects.filter(project=project, intern=intern, role=TeamRole.TEAM_LEAD,
                                           status=TeamMember.Status.ACTIVE)
        existing = next((m for m in active if lead_member_direction(m) == direction), None)
        if existing:
            return existing, False
        member = TeamMember(project=project, intern=intern, role=TeamRole.TEAM_LEAD, status=TeamMember.Status.ACTIVE,
                            group=getattr(project, 'group', None), joined_at=timezone.localdate(),
                            lead_direction='' if direction == own else direction)
        member.save()
        _log(project, 'Тимлид назначен', user, new_value=intern.full_name,
             reason=dict(TeamRole.choices).get(direction, 'направление не указано'))
    transaction.on_commit(lambda: _tell_staff(intern, f'Вы назначены тимлидом проекта «{project.name}» '
                                                      f'({dict(TeamRole.choices).get(direction, "направление")}).',
                                              f'lead-assigned:{member.pk}'))
    return member, True


def _tell_staff(intern, text, key):
    """Изменение назначений ПМ / тимлида — личное уведомление (через очередь бота)."""
    try:
        from apps.tgbot import notify

        notify.to_intern(intern, 'staff_assignment', text, dedup_key=key)
    except Exception:  # уведомление не должно ломать кадровую операцию
        import logging

        logging.getLogger(__name__).exception('Уведомление о назначении не поставлено (intern=%s)', intern.pk)


def promote_to_lead(intern: Intern, user) -> bool:
    """Повышение стажёра до штатного тимлида — одна атомарная операция.

    1. проверка руководителя; 2. текущий период стажировки закрывается с
    причиной «Повышен(а) до тимлида»; 3. участия стажёра в проектах
    закрываются (задачи, оценки, предупреждения не трогаются); 4. событие
    выпуска — журнал «Повышен(а) до тимлида» (основание graduation) и дата
    выпуска периода, если её ещё не было — повторно не выпускаем;
    5. кадровый статус «Работает в GeeksPro», должность «Тимлид».
    Проектные права — только после назначения на конкретный проект (assign_lead).
    Возвращает False, если человек уже тимлид.
    """
    from apps.dashboard.graduation import is_graduate
    from apps.interns import internships

    _require_head(user)
    with transaction.atomic():
        intern = _lock(intern)
        if intern.position == Position.TEAM_LEAD and intern.staff_status == StaffStatus.ACTIVE:
            return False
        if intern.is_archived or intern.status in (InternStatus.EXPELLED, InternStatus.CANCELLED):
            raise StaffError('Повысить можно только действующего стажёра или выпускника.')
        today = timezone.localdate()
        already_graduate = is_graduate(intern.pk)
        period = internships.current(intern)
        if period is not None:
            if not period.graduated_on and not already_graduate:
                period.graduated_on = today
            period.ended_on, period.end_reason = today, Internship.EndReason.PROMOTED
            period.save(update_fields=['ended_on', 'end_reason', 'graduated_on', 'updated_at'])
        ended = list(TeamMember.objects.select_for_update(of=('self',)).filter(
            intern=intern, status=TeamMember.Status.ACTIVE).exclude(role=TeamRole.TEAM_LEAD))
        for member in ended:
            _end(member, TeamMember.LeftReason.PROMOTED)
        was = intern.get_position_display()
        intern.position = Position.TEAM_LEAD
        intern.staff_status, intern.staff_role = StaffStatus.ACTIVE, StaffRole.LEAD
        intern.staff_since, intern.staff_until = today, None
        intern.save(update_fields=['position', 'staff_status', 'staff_role', 'staff_since', 'staff_until',
                                   'updated_at'])
        if intern.user_id and intern.user.role in ('intern', 'pm'):
            intern.user.role = 'team_lead'
            intern.user.save(update_fields=['role'])
        _log(intern, 'Повышен(а) до тимлида', user, old_value=was, new_value='Тимлид',
             reason=('стажировка завершена; вышел(а) из: ' + ', '.join(m.project.name for m in ended if m.project_id))
             if ended else 'стажировка завершена')
    return True


def dismiss(intern: Intern, user, reason: str, *, studio=False):
    """Прекращение работы тимлида / ПМ-сотрудника в GeeksPro (studio=True —
    перешёл в Geeks Studio). Назначения закрываются, учётка деактивируется,
    руководитель получает уведомления о проектах без тимлида."""
    _require_head(user)
    reason = (reason or '').strip()
    if not reason and not studio:
        raise StaffError('Укажите причину.')
    with transaction.atomic():
        intern = _lock(intern)
        final = StaffStatus.STUDIO if studio else StaffStatus.DISMISSED
        if intern.staff_status == final:
            raise StaffError('Уже оформлено.')
        members = list(TeamMember.objects.select_for_update(of=('self',)).filter(
            intern=intern, status=TeamMember.Status.ACTIVE))
        left = TeamMember.LeftReason.EMPLOYED_STUDIO if studio else TeamMember.LeftReason.DISMISSED
        for member in members:
            _end(member, left)
        intern.staff_status = final
        intern.staff_role = intern.staff_role or (StaffRole.LEAD if intern.position == Position.TEAM_LEAD else '')
        intern.staff_until = timezone.localdate()
        intern.save(update_fields=['staff_status', 'staff_role', 'staff_until', 'updated_at'])
        if intern.user_id and intern.user.is_active and not intern.user.is_superuser \
                and intern.user.role not in ('head', 'administrator'):
            intern.user.is_active = False  # старая сессия больше не пустит
            intern.user.save(update_fields=['is_active'])
        _log(intern, 'Трудоустроен(а) в Geeks Studio' if studio else 'Прекратил(а) работу в GeeksPro', user,
             reason=reason, new_value=', '.join(m.project.name for m in members if m.project_id))
        project_ids = [m.project_id for m in members if m.project_id and m.role == TeamRole.TEAM_LEAD]
        transaction.on_commit(lambda: notify_uncovered(project_ids))
    return members


# --------------------------------------------------------- без тимлида

# У QA тимлидов в GeeksPro нет (решение руководителя, 11F) — «QA без тимлида» не проблема.
NO_LEAD_DIRECTIONS = {TeamRole.QA}


def uncovered(project_ids=None) -> list[dict]:
    """Идущие проекты, где в направлении есть стажёры, а тимлида этого направления нет."""
    from apps.projects.models import Project, ProjectStatus
    from apps.teams.rules import DIRECTION_ROLES

    projects = Project.objects.active().filter(status=ProjectStatus.ACTIVE)
    if project_ids is not None:
        projects = projects.filter(pk__in=project_ids)
    projects = list(projects)
    labels = dict(TeamRole.choices)
    # Этап 10B: одна выборка на все проекты (было по три запроса на проект — N+1)
    directions, covered = {}, {}
    for member in (TeamMember.objects.filter(project__in=projects, status=TeamMember.Status.ACTIVE,
                                             intern__isnull=False)
                   .filter(role__in=[*DIRECTION_ROLES, TeamRole.TEAM_LEAD]).select_related('intern__specialization')):
        if member.role == TeamRole.TEAM_LEAD:
            covered.setdefault(member.project_id, set()).add(lead_member_direction(member))
        else:
            directions.setdefault(member.project_id, set()).add(member.role)
    result = []
    for project in projects:
        for role in sorted(directions.get(project.pk, set()) - covered.get(project.pk, set()) - NO_LEAD_DIRECTIONS):
            result.append({'project': project, 'role': role, 'label': labels.get(role, role)})
    return result


def notify_uncovered(project_ids):
    from apps.notifications.models import NotificationLevel
    from apps.notifications.services import notify

    for item in uncovered(project_ids):
        project = item['project']
        notify(f'Нужен тимлид: «{project.name}» · {item["label"]}', level=NotificationLevel.WARNING,
               description='Направление осталось без тимлида — назначьте замену.',
               url=f'{project.get_absolute_url()}?tab=team', dedup_key=f'no-lead:{project.pk}:{item["role"]}')

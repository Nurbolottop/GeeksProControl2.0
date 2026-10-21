"""Правила состава команд 2.0 (проверяются при любом ручном назначении).

* Новый стажёр (без подтверждённого выпуска) — только один проект; вторая
  параллельная стажировка запрещена.
* Старый стажёр (подтверждённый выпускник) — максимум два проекта одновременно.
* В одном направлении проекта — максимум четыре старых стажёра (жёстко).
  Для новых ориентир тоже четыре, но больше — можно (только предупреждение).

Считаются участия в направлениях и в роли ПМ (стажёр направления PM — такой
же стажёр); тимлиды — сотрудники и под эти правила не попадают.

Закрытый набор (UI-03B): новый участник направления не попадает в проект,
пока руководитель не откроет набор, — по любому пути (формы руководителя и
ПМ, карточка стажёра, анкета тимлида, старая ссылка, повторная отправка).
Кто уже в команде, работает дальше. ПМ и тимлидов назначает руководство —
это не набор, их назначение не блокируется.
"""
from django.core.exceptions import ValidationError

from apps.teams.models import TeamMember, TeamRole

DIRECTION_ROLES = (TeamRole.BACKEND, TeamRole.FRONTEND, TeamRole.MOBILE, TeamRole.UXUI, TeamRole.QA, TeamRole.OTHER)
# Правила состава (1/2 проекта, 4 выпускника в направлении) действуют и для
# направления PM (UI-04B): стажёр-ПМ — такой же стажёр. Тимлиды — сотрудники.
RULE_ROLES = (*DIRECTION_ROLES, TeamRole.PROJECT_MANAGER)
NEW_MAX_PROJECTS = 1
OLD_MAX_PROJECTS = 2
OLD_MAX_PER_DIRECTION = 4
NEW_TARGET_PER_DIRECTION = 4


def _active_direction_members():
    return TeamMember.objects.filter(
        status=TeamMember.Status.ACTIVE, role__in=RULE_ROLES, intern__isnull=False, project__isnull=False,
    )


def internship_error(intern, project=None) -> str | None:
    """Стажировка прекращена или заморожена — в новый проект не вступают.

    Уже активный участник проекта (правка участия) — не вступление.
    """
    from apps.interns.models import InternStatus

    if project is not None and TeamMember.objects.filter(project=project, intern=intern,
                                                         status=TeamMember.Status.ACTIVE).exists():
        return None
    if intern.status in (InternStatus.EXPELLED, InternStatus.CANCELLED):
        return (f'{intern.full_name}: стажировка прекращена ({intern.get_status_display().lower()}) — '
                'вступить можно только после повторной стажировки (продажи + руководитель).')
    if intern.status == InternStatus.PAUSED:
        return f'{intern.full_name}: стажировка заморожена — сначала возвращение из заморозки.'
    return None


def recruitment_error(intern, project, role) -> str | None:
    """Почему intern не может вступить в project (набор закрыт / проект не идёт).

    Уже активный участник проекта не вступает заново — для него None.
    """
    from apps.projects.models import ProjectStatus
    from apps.teams.selectors import staff_intern_ids

    if intern is None or project is None or role not in DIRECTION_ROLES or intern.pk in staff_intern_ids():
        return None
    if TeamMember.objects.filter(project=project, intern=intern, status=TeamMember.Status.ACTIVE).exists():
        return None
    if project.status != ProjectStatus.ACTIVE:
        return f'Проект «{project.name}» не идёт ({project.get_status_display().lower()}) — новых участников не добавляем.'
    if not project.recruitment_open:
        return f'Набор в проект «{project.name}» закрыт — новых участников не добавляем, пока руководитель не откроет набор.'
    return None


def check_assignment(intern, project, role, exclude_member_pk=None, joining=True) -> tuple[list[str], list[str]]:
    """(ошибки, предупреждения) для назначения intern в project на role.

    joining=False — участие сохраняется не активным (снятие, правка вышедшего):
    набор и лимиты тогда не проверяем.
    """
    from apps.dashboard.graduation import compute
    from apps.teams.selectors import staff_intern_ids

    if intern is None or role not in RULE_ROLES or intern.pk in staff_intern_ids() or not joining:
        return [], []
    errors, warnings = [], []
    stopped = internship_error(intern, project)
    if stopped:
        errors.append(stopped)
    closed = recruitment_error(intern, project, role)
    if closed:
        errors.append(closed)
    others = _active_direction_members().filter(intern=intern).exclude(project=project)
    if exclude_member_pk:
        others = others.exclude(pk=exclude_member_pk)
    projects_elsewhere = others.values('project_id').distinct().count()
    graduates = compute([intern.pk]).ids
    if intern.pk in graduates:
        if projects_elsewhere >= OLD_MAX_PROJECTS:
            errors.append(f'{intern.full_name} уже в {projects_elsewhere} проектах — старый стажёр может быть максимум в {OLD_MAX_PROJECTS}.')
        same_direction = _active_direction_members().filter(project=project, role=role).exclude(intern=intern)
        if exclude_member_pk:
            same_direction = same_direction.exclude(pk=exclude_member_pk)
        old_ids = compute(list(same_direction.values_list('intern_id', flat=True))).ids
        if len(old_ids) >= OLD_MAX_PER_DIRECTION:
            errors.append(f'В направлении уже {len(old_ids)} старых стажёра — максимум {OLD_MAX_PER_DIRECTION}.')
    else:
        if projects_elsewhere >= NEW_MAX_PROJECTS:
            errors.append(f'{intern.full_name} — новый стажёр и уже участвует в проекте; вторая параллельная стажировка запрещена.')
        same_direction = _active_direction_members().filter(project=project, role=role).exclude(intern=intern)
        if same_direction.count() >= NEW_TARGET_PER_DIRECTION:
            warnings.append(f'В направлении уже {same_direction.count()} человек — ориентир {NEW_TARGET_PER_DIRECTION}.')
    return errors, warnings


def validate_assignment(intern, project, role, exclude_member_pk=None, joining=True) -> list[str]:
    """Ошибки — ValidationError, предупреждения возвращаются."""
    errors, warnings = check_assignment(intern, project, role, exclude_member_pk, joining)
    if errors:
        raise ValidationError(errors)
    return warnings


def lock_and_recheck(project, intern, role) -> str | None:
    """Повторная проверка набора под блокировкой (стажёр, затем проект).

    Вызывается внутри transaction.atomic() прямо перед сохранением участия:
    закрытие набора (set_recruitment) берёт блокировку проекта, а два
    одновременных зачисления одного человека — блокировку его строки.
    Порядок всегда «стажёр → проект», чтобы не было взаимных блокировок.
    """
    from apps.interns.models import Intern
    from apps.projects.models import Project

    if intern is not None and intern.pk:
        Intern.objects.select_for_update().filter(pk=intern.pk).first()
    locked = Project.objects.select_for_update().get(pk=project.pk)
    return recruitment_error(intern, locked, role)


def lock_and_check(project, intern, role, exclude_member_pk=None) -> list[str]:
    """Все правила (набор, новичок/выпускник, лимиты) под блокировкой —
    защита от одновременного зачисления в два проекта."""
    closed = lock_and_recheck(project, intern, role)
    errors, _warnings = check_assignment(intern, project, role, exclude_member_pk)
    if closed and closed not in errors:
        errors.insert(0, closed)
    return errors

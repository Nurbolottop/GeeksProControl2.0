"""Загрузка людей и предупреждение о перегрузе (ТЗ §11, §22)."""
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.teams.models import TeamMember


def activate_intern_membership(member: TeamMember, *, user=None, reason: str = '') -> None:
    """Стажёра назначили/переназначили на активное место в команде — где
    бы это ни произошло (карточка стажёра, портал ПМ, портал тимлида,
    общая админка команд).

    Раньше эту логику (снять статус выпускника, перевести «Ожидает
    стажировки»/«Готов к распределению» в «Активный») повторяли только
    в apps.interns.services.join_project_from_form и
    apps.interns.views.intern_project_add — остальные места, где
    TeamMember создаётся или у него меняется intern (member_add/
    member_edit в pm_portal, lead_portal, apps.teams), её не делали:
    человек оставался «выпускником на проверке» с уже активным
    проектом в команде.
    """
    from apps.interns.models import InternStatus

    intern = member.intern
    if intern is None or member.status != TeamMember.Status.ACTIVE:
        return
    update_fields = []
    if intern.status in (InternStatus.WAITING, InternStatus.READY):
        intern.status = InternStatus.ACTIVE
        update_fields.append('status')
    if intern.graduate_status:
        from apps.audit.services import log as audit_log

        audit_log(
            intern, 'Статус выпускника снят',
            old_value=intern.get_graduate_status_display(),
            reason=reason or f'добавлен(а) в команду «{member.project}»',
            user=user,
        )
        intern.graduate_status = ''
        update_fields.append('graduate_status')
    if update_fields:
        intern.save(update_fields=[*update_fields, 'updated_at'])


def person_workload(*, user=None, intern=None, exclude_pk=None) -> int:
    """Суммарная загрузка человека по активным участиям в командах."""
    qs = TeamMember.objects.filter(status=TeamMember.Status.ACTIVE)
    if user is not None:
        qs = qs.filter(user=user)
    elif intern is not None:
        qs = qs.filter(intern=intern)
    else:
        return 0
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.aggregate(total=Sum('workload'))['total'] or 0


def leave_team(member: TeamMember, reason: str = '') -> None:
    """Снять человека с проекта — не удаляем запись, чтобы не терять
    историю участия, просто помечаем «Вышел» (+ причина, если её
    выбрали при снятии).

    Если причина — «Проект завершён», это по сути то же самое
    завершение, что и apps.projects.services.release_team (там
    команду освобождают всю разом, через смену статуса проекта), но
    ПМ/тимлиды на практике часто просто снимают людей по одному, а сам
    проект в «Завершён» не переводят. Раньше в этом случае человек не
    попадал в «Выпускники»: снятие с проекта — не то же самое, что
    смена статуса проекта, а graduate_status выставлялся только там.
    """
    member.status = TeamMember.Status.LEFT
    member.left_at = timezone.localdate()
    if reason in TeamMember.LeftReason.values:
        member.left_reason = reason
    member.save(update_fields=['status', 'left_at', 'left_reason', 'updated_at'])
    if member.intern_id and member.project_id:
        from apps.tasks.workflow import release_stale

        release_stale(intern_ids=[member.intern_id], project_ids=[member.project_id],
                      reason=member.get_left_reason_display().lower() if member.left_reason else 'снят(а) с проекта')
    if reason == TeamMember.LeftReason.PROJECT_ENDED and member.intern_id:
        _graduate_after_leaving(member)


def still_on_a_live_project(intern_id: int, exclude_member_id: int | None = None) -> bool:
    """Человек продолжает работать: есть активное участие в идущем проекте.

    Завершение одного проекта не делает выпускником того, кто занят на
    других: у людей бывает два-три проекта сразу.
    """
    from apps.projects.models import ProjectStatus

    memberships = TeamMember.objects.filter(
        intern_id=intern_id, status=TeamMember.Status.ACTIVE,
        project__status=ProjectStatus.ACTIVE,
    )
    if exclude_member_id:
        memberships = memberships.exclude(pk=exclude_member_id)
    return memberships.exists()


def _graduate_after_leaving(member: TeamMember) -> None:
    from apps.interns.models import GraduateStatus, InternStatus
    from apps.teams.selectors import lead_intern_ids

    intern = member.intern
    if intern.pk in lead_intern_ids():
        return
    if still_on_a_live_project(intern.pk, exclude_member_id=member.pk):
        return
    update_fields = []
    if intern.status == InternStatus.ACTIVE:
        intern.status = InternStatus.READY
        update_fields.append('status')
    if not intern.graduate_status:
        intern.graduate_status = GraduateStatus.PENDING
        update_fields.append('graduate_status')
    if update_fields:
        intern.save(update_fields=[*update_fields, 'updated_at'])


def workload_band(total: int) -> tuple[str, str]:
    """Диапазоны загрузки (ТЗ §11): (код, подпись)."""
    if total > 100:
        return 'overload', 'Перегруз'
    if total > 80:
        return 'high', 'Высокая загрузка'
    if total > 50:
        return 'normal', 'Нормальная загрузка'
    return 'free', 'Свободен'


def save_membership(member: TeamMember) -> str | None:
    """Сохранить участие с повторной проверкой правил под блокировкой.

    Возвращает текст ошибки (участие не сохранено) или None. Формы уже
    проверили всё в clean(); здесь — защита от гонок: закрытие набора,
    одновременное зачисление того же человека в другой проект.
    """
    from apps.teams.rules import lock_and_check

    with transaction.atomic():
        if member.status == TeamMember.Status.ACTIVE and member.project_id and member.intern_id:
            errors = lock_and_check(member.project, member.intern, member.role, exclude_member_pk=member.pk)
            if errors:
                return ' '.join(errors)
        member.save()
    return None

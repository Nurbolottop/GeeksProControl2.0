"""Заморозка и возвращение стажёра (UI-03C).

1. Стажёр подаёт заявку с причиной (Telegram-бот) — или руководитель
   оформляет её со слов стажёра.
2. Решает только руководитель. Пока заявка не одобрена, ничего не меняется.
3. Одобрение заморозки — сразу и атомарно:
   * все активные участия закрываются с причиной «Стажировка заморожена»
     (записи не удаляются — история участия остаётся);
   * незавершённые задачи стажёра в этих проектах возвращаются
     «К выполнению» (статус «Новая»); выполненные остаются выполненными;
     автор, исполнитель, комментарии и вложения не трогаются;
   * статус стажёра — «Приостановлен» (заморожен), в журнале — событие
     «Стажировка заморожена»: по нему apps.dashboard.graduation знает, что
     закрытие бывшего проекта стажёра не выпускает.
4. Возвращение — отдельная заявка. После одобрения стажёр «Ожидает
   стажировки» и сам выбирает новый проект (choose_project) по действующим
   правилам: открытый набор, ограничения новичков и выпускников, лимиты.
   Прежнее участие автоматически не восстанавливается. Стажёру направления
   PM проект назначает руководитель.

Защита: строки заявки, стажёра и участий блокируются (select_for_update);
повторное одобрение, повторная заморозка и гонка с закрытием проекта
отклоняются или видят уже обновлённые данные. Telegram — после commit.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.interns.models import Intern, InternshipRequest, InternStatus

Kind = InternshipRequest.Kind
Status = InternshipRequest.Status
FREEZABLE = (InternStatus.ACTIVE, InternStatus.READY, InternStatus.WAITING)
UNFINISHED_TASKS = ('in_progress', 'review')


class FreezeError(ValidationError):
    pass


def _require_head(user):
    from apps.accounts.access import has_full_access

    if not has_full_access(user):
        raise PermissionDenied('Заморозки и возвращения утверждает руководитель.')


def _create(intern, kind, reason, source, user):
    reason = (reason or '').strip()
    if not reason:
        raise FreezeError('Укажите причину.')
    try:
        with transaction.atomic():
            return InternshipRequest.objects.create(
                intern=intern, kind=kind, reason=reason[:2000], source=source,
                requested_by=user if getattr(user, 'is_authenticated', False) else None,
            )
    except IntegrityError as error:
        raise FreezeError('Такая заявка уже ждёт решения руководителя.') from error


def _notify_head(req):
    from apps.notifications.models import NotificationLevel
    from apps.notifications.services import notify

    notify(
        f'{req.get_kind_display()}: {req.intern.full_name}', level=NotificationLevel.WARNING,
        description=req.reason[:300], url=_requests_url(), dedup_key=f'internship-request:{req.pk}',
    )


def _requests_url():
    from django.urls import reverse

    return reverse('interns:internship_requests')


def request_freeze(intern: Intern, reason: str, *, source=InternshipRequest.Source.BOT, user=None):
    if intern.is_archived or intern.status not in FREEZABLE:
        raise FreezeError('Заморозить можно только идущую или ожидающую стажировку.')
    req = _create(intern, Kind.FREEZE, reason, source, user)
    _notify_head(req)
    return req


def request_return(intern: Intern, reason: str, *, source=InternshipRequest.Source.BOT, user=None):
    if intern.is_archived or intern.status != InternStatus.PAUSED:
        raise FreezeError('Вернуться можно только из заморозки.')
    req = _create(intern, Kind.RETURN, reason, source, user)
    _notify_head(req)
    return req


@dataclass
class FreezeResult:
    projects: list
    tasks_reset: int


def end_participation(intern: Intern, user, left_reason: str, why: str, project_ids=None) -> FreezeResult:
    """Закрыть все активные участия и вернуть незавершённые задачи «К выполнению».

    Общая часть заморозки, отчисления и добровольной отмены. Внутри
    транзакции, стажёр уже заблокирован. Записи участий не удаляются;
    выполненные задачи, авторы, исполнители и комментарии не меняются.
    """
    from apps.audit.services import log as audit_log
    from apps.projects.models import Project
    from apps.tasks.models import Task, TaskStatus
    from apps.teams.models import TeamMember

    today = timezone.localdate()
    # Блокируем только строки участий (of=self): закрытие проекта берёт те же
    # строки — кто второй, тот увидит уже обновлённый статус.
    members = TeamMember.objects.select_for_update(of=('self',)).filter(intern=intern, status=TeamMember.Status.ACTIVE)
    if project_ids is not None:
        members = members.filter(project_id__in=project_ids)  # только выбранные проекты
    members = list(members.order_by('pk'))
    project_ids = [m.project_id for m in members if m.project_id]
    TeamMember.objects.filter(pk__in=[m.pk for m in members]).update(
        status=TeamMember.Status.LEFT, left_at=today, left_reason=left_reason,
    )
    projects = list(Project.objects.filter(pk__in=project_ids).order_by('name'))
    reset = 0
    if intern.user_id and project_ids:
        # старые задачи с одним исполнителем-учёткой (без TaskAssignment)
        tasks = list(Task.objects.select_for_update(of=('self',)).filter(
            assignee_id=intern.user_id, project_id__in=project_ids, status__in=UNFINISHED_TASKS,
            assignments__isnull=True,
        ))
        for task in tasks:
            audit_log(task, 'Возвращена «К выполнению»', old_value=task.get_status_display(),
                      new_value=TaskStatus.NEW.label, reason=why, user=user)
        reset = Task.objects.filter(pk__in=[t.pk for t in tasks]).update(status=TaskStatus.NEW)
    label = dict(TeamMember.LeftReason.choices).get(left_reason, left_reason).lower()
    if project_ids:
        from apps.tasks.workflow import release_stale

        reset += release_stale(intern_ids=[intern.pk], project_ids=project_ids, reason=label, user=user)
    for project in projects:
        audit_log(project, f'Участник вышел: {label}', new_value=intern.full_name, user=user)
    _notify_leads(intern, projects, label)
    return FreezeResult(projects=projects, tasks_reset=reset)


def _apply_freeze(intern: Intern, user, reason: str) -> FreezeResult:
    """Внутри транзакции, стажёр уже заблокирован."""
    from apps.audit.services import log as audit_log
    from apps.teams.models import TeamMember

    result = end_participation(intern, user, TeamMember.LeftReason.FROZEN, 'стажировка исполнителя заморожена')
    old_status = intern.get_status_display()
    intern.status = InternStatus.PAUSED
    intern.save(update_fields=['status', 'updated_at'])
    audit_log(intern, 'Стажировка заморожена', old_value=old_status,
              new_value=InternStatus.PAUSED.label, reason=reason, user=user)
    return result


def _notify_leads(intern, projects, label='стажировка заморожена'):
    from apps.notifications.models import NotificationLevel
    from apps.notifications.services import notify
    from apps.teams.models import TeamMember, TeamRole

    leads = Intern.objects.filter(
        team_memberships__project__in=projects, team_memberships__role=TeamRole.TEAM_LEAD,
        team_memberships__status=TeamMember.Status.ACTIVE,
    ).distinct()
    for lead in leads:
        notify(f'{intern.full_name}: {label}', level=NotificationLevel.WARNING, intern=lead,
               description='Стажёр вышел из команды — место освободилось, незавершённые задачи вернулись «К выполнению».',
               dedup_key=f'left:{intern.pk}:{label}:{timezone.localdate():%Y%m%d}')


def approve(req: InternshipRequest, user, comment: str = '') -> InternshipRequest:
    _require_head(user)
    from apps.audit.services import log as audit_log

    with transaction.atomic():
        req = InternshipRequest.objects.select_for_update().get(pk=req.pk)
        if req.status != Status.PENDING:
            raise FreezeError('Заявка уже рассмотрена.')
        intern = Intern.objects.select_for_update().get(pk=req.intern_id)
        if req.kind == Kind.ENROLL:
            raise FreezeError('Новую стажировку подтверждает только отдел продаж (Telegram-группа продаж).')
        if req.kind == Kind.FREEZE:
            if intern.status == InternStatus.PAUSED:
                raise FreezeError('Стажировка уже заморожена.')
            if intern.is_archived or intern.status not in FREEZABLE:
                raise FreezeError('Стажировку в этом статусе не замораживают.')
            result = _apply_freeze(intern, user, req.reason)
            req.left_projects = '\n'.join(p.name for p in result.projects)
            req.tasks_reset = result.tasks_reset
        elif req.kind == Kind.CANCEL:
            from apps.interns import discipline

            result = discipline.apply_cancel(intern, user, req.reason)
            req.left_projects = '\n'.join(p.name for p in result.projects)
            req.tasks_reset = result.tasks_reset
        elif req.kind == Kind.REENROLL:
            from apps.interns import discipline

            discipline.apply_reenroll(req, intern, user)
        elif req.kind == Kind.LEAVE_PROJECT:
            from apps.interns import discipline

            result = discipline.apply_leave(req, intern, user)
            req.left_projects = '\n'.join(p.name for p in result.projects)
            req.tasks_reset = result.tasks_reset
        else:
            if intern.status != InternStatus.PAUSED:
                raise FreezeError('Стажёр не заморожен — возвращать неоткуда.')
            intern.status = InternStatus.WAITING
            intern.save(update_fields=['status', 'updated_at'])
            audit_log(intern, 'Стажировка возобновлена', old_value=InternStatus.PAUSED.label,
                      new_value=InternStatus.WAITING.label, reason=req.reason, user=user)
        req.status = Status.APPROVED
        req.decided_by, req.decided_at = user, timezone.now()
        req.decision_comment = (comment or '').strip()
        req.save()
        transaction.on_commit(lambda: _tell_intern(req))
    return req


def reject(req: InternshipRequest, user, comment: str = '') -> InternshipRequest:
    _require_head(user)
    with transaction.atomic():
        req = InternshipRequest.objects.select_for_update().get(pk=req.pk)
        if req.status != Status.PENDING:
            raise FreezeError('Заявка уже рассмотрена.')
        req.status = Status.REJECTED
        req.decided_by, req.decided_at = user, timezone.now()
        req.decision_comment = (comment or '').strip()
        req.save()
        transaction.on_commit(lambda: _tell_intern(req))
    return req


def freeze_by_head(intern: Intern, reason: str, user) -> InternshipRequest:
    """Руководитель оформляет заморозку со слов стажёра и сразу одобряет."""
    _require_head(user)
    pending = intern.internship_requests.filter(kind=Kind.FREEZE, status=Status.PENDING).first()
    req = pending or request_freeze(intern, reason, source=InternshipRequest.Source.HEAD, user=user)
    return approve(req, user, comment=reason if pending else '')


def return_by_head(intern: Intern, reason: str, user) -> InternshipRequest:
    _require_head(user)
    pending = intern.internship_requests.filter(kind=Kind.RETURN, status=Status.PENDING).first()
    req = pending or request_return(intern, reason, source=InternshipRequest.Source.HEAD, user=user)
    return approve(req, user, comment=reason if pending else '')


# ------------------------------------------------------------ выбор проекта

def is_pm_direction(intern: Intern) -> bool:
    spec = intern.specialization
    return bool(spec and spec.name == 'PM')


def open_return(intern: Intern) -> InternshipRequest | None:
    """Одобренное возвращение (или подтверждённая продажами новая стажировка,
    этап 8A), по которому проект ещё не выбран."""
    return intern.internship_requests.filter(
        kind__in=[Kind.RETURN, Kind.ENROLL], status=Status.APPROVED, chosen_project__isnull=True,
    ).order_by('-decided_at').first()


def _role(intern):
    from apps.teams.forms import ROLE_BY_SPECIALIZATION
    from apps.teams.models import TeamRole

    spec = intern.specialization
    return ROLE_BY_SPECIALIZATION.get(spec.name if spec else '', TeamRole.OTHER)


def available_projects(intern: Intern) -> list:
    """Куда стажёр может вступить сейчас — по тем же правилам, что формы."""
    from apps.projects.services import projects_open_for_recruitment
    from apps.teams.rules import check_assignment

    if is_pm_direction(intern) or open_return(intern) is None or intern.status == InternStatus.PAUSED:
        return []
    role = _role(intern)
    return [p for p in projects_open_for_recruitment().order_by('name')
            if not check_assignment(intern, p, role)[0]]


def choose_project(intern: Intern, project):
    """Стажёр после одобренного возвращения выбирает проект (один раз)."""
    from apps.audit.services import log as audit_log
    from apps.teams.models import TeamMember
    from apps.teams.rules import check_assignment, lock_and_recheck

    if is_pm_direction(intern):
        raise FreezeError('Стажёру направления PM проект назначает руководитель.')
    role = _role(intern)
    with transaction.atomic():
        intern = Intern.objects.select_for_update().get(pk=intern.pk)
        req = open_return(intern)
        if req is None:
            raise FreezeError('Выбор проекта доступен после одобренного возвращения или подтверждения продаж.')
        req = InternshipRequest.objects.select_for_update().get(pk=req.pk)
        if req.chosen_project_id or intern.status == InternStatus.PAUSED:
            raise FreezeError('Проект уже выбран.')
        closed = lock_and_recheck(project, intern, role)
        errors, _ = check_assignment(intern, project, role)
        if closed or errors:
            raise FreezeError([closed] if closed else errors)
        member = TeamMember.objects.create(
            project=project, intern=intern, role=role, status=TeamMember.Status.ACTIVE,
            group=getattr(project, 'group', None), joined_at=timezone.localdate(),
        )
        intern.status = InternStatus.ACTIVE
        intern.save(update_fields=['status', 'updated_at'])
        req.chosen_project, req.chosen_at = project, timezone.now()
        req.save(update_fields=['chosen_project', 'chosen_at', 'updated_at'])
        audit_log(intern, 'Выбрал(а) первый проект' if req.kind == Kind.ENROLL else 'Выбрал(а) проект после возвращения',
                  new_value=project.name)
    return member


# ------------------------------------------------------------ сообщения

def _tell_intern(req: InternshipRequest):
    import logging

    intern = req.intern
    if req.status == Status.REJECTED:
        text = f'Заявка «{req.get_kind_display().lower()}» отклонена руководителем.'
        if req.decision_comment:
            text += f'\n\nКомментарий: {req.decision_comment}'
    elif req.kind == Kind.CANCEL:
        text = 'Стажировка отменена по вашей заявке. История и оценки сохранены.'
    elif req.kind == Kind.REENROLL:
        text = 'Повторная стажировка одобрена. Проект назначат по действующим правилам — напишем сюда.'
    elif req.kind == Kind.LEAVE_PROJECT:
        text = 'Выход из проекта одобрен. Участие во втором проекте не меняется.'
    elif req.kind == Kind.FREEZE:
        text = ('Стажировка заморожена. Вы вышли из команд проектов. Когда будете готовы '
                'продолжить — отправьте /return и укажите причину.')
    elif is_pm_direction(intern):
        text = 'Возвращение одобрено. Проект для направления PM назначит руководитель — напишем сюда.'
    else:
        text = 'Возвращение одобрено! Выберите новый проект: /projects'
    try:
        from apps.tgbot import notify

        notify.to_intern(intern, 'request_decided', text, dedup_key=f'request-decided:{req.pk}:{req.status}')
    except Exception:
        logging.getLogger(__name__).exception('Не удалось поставить уведомление о решении (intern=%s)', intern.pk)

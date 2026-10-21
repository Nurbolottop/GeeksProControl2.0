"""Задачи проекта: доска из четырёх состояний, исполнители и проверка работ (этап 7A).

Права проверяются здесь, а не во views:
* руководитель — все задачи всех проектов;
* активный ПМ проекта — все задачи своего проекта;
* активный тимлид проекта — задачи своего направления (и общие, без
  направления); назначает только стажёров своего направления;
* закрытый / отменённый проект — только чтение (кроме руководителя);
  сдавать работу в нём нельзя никому.

Исполнители — участники проекта (TaskAssignment), у каждого свой результат.
Общий статус задачи выводится из активных назначений (recompute):
«Готово» — только когда принят результат каждого активного исполнителя.
«На проверке» и «Готово» для задачи с исполнителями ставятся только сдачей
работы и ручной проверкой, не кнопкой.

Сдача (submit_work) — для Telegram-бота: обязательны задача, подтверждённый
исполнитель и скриншот. Проверка (review) — тимлид направления, ПМ или
руководитель: «принять» или «вернуть на доработку» с причиной; прежние
попытки и материалы остаются.

Выход из проекта (заморозка, отчисление, повышение, увольнение, снятие с
проекта, завершение проекта) — release_stale: незавершённые назначения
закрываются, задача возвращается в очередь, принятые результаты и история
сохраняются, назначения других исполнителей не трогаются.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.projects.models import ProjectStatus
from apps.tasks.models import (
    BOARD_STATUSES, DIRECTION_CHOICES, Task, TaskAssignment, TaskEvent, TaskStatus, TaskSubmission,
)

WRITABLE_PROJECT = (ProjectStatus.ACTIVE, ProjectStatus.PAUSED)
SCREENSHOT_EXTENSIONS = ('png', 'jpg', 'jpeg', 'webp', 'gif', 'heic')
DIRECTIONS = dict(DIRECTION_CHOICES)

# Ручные переходы задачи без исполнителей (как раньше — кнопками)
TRANSITIONS = {
    TaskStatus.NEW: {TaskStatus.IN_PROGRESS},
    TaskStatus.IN_PROGRESS: {TaskStatus.NEW, TaskStatus.REVIEW},
    TaskStatus.REVIEW: {TaskStatus.IN_PROGRESS, TaskStatus.DONE},
    TaskStatus.DONE: {TaskStatus.IN_PROGRESS},
    TaskStatus.CANCELLED: {TaskStatus.NEW},  # восстановить старую отменённую
}
# С исполнителями вручную — только «К выполнению» ↔ «В работе»
ASSIGNED_TRANSITIONS = {
    TaskStatus.NEW: {TaskStatus.IN_PROGRESS},
    TaskStatus.IN_PROGRESS: {TaskStatus.NEW},
}


class TaskError(ValidationError):
    pass


@dataclass(frozen=True)
class Actor:
    kind: str  # head / pm / lead
    direction: str = ''

    @property
    def is_head(self):
        return self.kind == 'head'


def actor(user, project) -> Actor | None:
    """Кем пользователь приходится проекту — по активным назначениям."""
    from apps.accounts.access import has_full_access
    from apps.teams.forms import ROLE_BY_SPECIALIZATION
    from apps.teams.models import TeamMember, TeamRole

    if user is None or not user.is_authenticated:
        return None
    if has_full_access(user):
        return Actor('head')
    members = TeamMember.objects.filter(project=project, intern__user=user, status=TeamMember.Status.ACTIVE)
    if members.filter(role=TeamRole.PROJECT_MANAGER).exists():
        return Actor('pm')
    lead = members.filter(role=TeamRole.TEAM_LEAD).select_related('intern__specialization').first()
    if lead:
        spec = lead.intern.specialization.name if lead.intern and lead.intern.specialization_id else ''
        return Actor('lead', ROLE_BY_SPECIALIZATION.get(spec, ''))
    return None


def _writable(project, who: Actor) -> bool:
    return who.is_head or project.status in WRITABLE_PROJECT


def can_manage(who: Actor | None, task) -> bool:
    if who is None or not _writable(task.project, who):
        return False
    if who.kind in ('head', 'pm'):
        return True
    return bool(who.direction) and task.direction in ('', who.direction)


def _require(user, project, task=None) -> Actor:
    who = actor(user, project)
    if who is None:
        raise PermissionDenied('Нет доступа к задачам этого проекта.')
    if not _writable(project, who):
        raise TaskError('Проект закрыт — задачи можно только смотреть.')
    if task is not None and not can_manage(who, task):
        raise PermissionDenied('Это задача другого направления.')
    return who


def event(task, kind, user=None, assignment=None, old='', new='', text=''):
    return TaskEvent.objects.create(task=task, kind=kind, user=user, assignment=assignment,
                                    old_value=str(old)[:255], new_value=str(new)[:255], text=text)


def _lock(task) -> Task:
    return Task.objects.select_for_update(of=('self',)).select_related('project').get(pk=task.pk)


def _touch(project):
    project.last_activity_at = timezone.now()
    project.save(update_fields=['last_activity_at', 'updated_at'])


def _set_status(task, status, user=None, text=''):
    if status == task.status:
        return False
    old = task.get_status_display()
    task.status = status
    task.completed_at = (task.completed_at or timezone.localdate()) if status == TaskStatus.DONE else None
    task.save(update_fields=['status', 'completed_at', 'updated_at'])
    event(task, TaskEvent.Kind.STATUS, user, old=old, new=task.get_status_display(), text=text)
    return True


def recompute(task, user=None, text=''):
    """Общий статус задачи из результатов активных исполнителей."""
    assignments = list(task.assignments.all())
    if not assignments:
        return task.status
    states = [a.status for a in assignments if a.is_active]
    if not states:
        status = TaskStatus.NEW  # в очереди: нужен исполнитель
    elif all(s == TaskStatus.DONE for s in states):
        status = TaskStatus.NEW if task.needs_reassign else TaskStatus.DONE  # часть работы ждёт исполнителя
    elif all(s in (TaskStatus.REVIEW, TaskStatus.DONE) for s in states):
        status = TaskStatus.REVIEW
    elif any(s != TaskStatus.NEW for s in states):
        status = TaskStatus.IN_PROGRESS
    else:
        status = TaskStatus.NEW
    _set_status(task, status, user, text)
    return status


# ------------------------------------------------------------ задачи

def create_task(project, user, *, title, description='', direction='', priority='medium', deadline=None,
                assignees=()):
    from apps.tasks.models import TaskPriority

    title = (title or '').strip()[:255]
    if not title:
        raise TaskError('Напишите, что нужно сделать.')
    with transaction.atomic():
        who = _require(user, project)
        if who.kind == 'lead':
            if not who.direction:
                raise PermissionDenied('У тимлида не указано направление.')
            direction = who.direction  # тимлид ставит задачи только своему направлению
        if direction and direction not in DIRECTIONS:
            raise TaskError('Неизвестное направление.')
        task = Task.objects.create(
            project=project, author=user, title=title, description=(description or '').strip(),
            direction=direction or '', priority=priority if priority in TaskPriority.values else 'medium',
            deadline=deadline,
        )
        event(task, TaskEvent.Kind.CREATED, user, new=task.title)
        for intern in assignees:
            assign(task, intern, user)
        _touch(project)
    return task


def update_task(task, user, *, title, description, priority, deadline, direction=None):
    from apps.tasks.models import TaskPriority

    with transaction.atomic():
        task = _lock(task)
        who = _require(user, task.project, task)
        title = (title or '').strip()[:255]
        if not title:
            raise TaskError('Название задачи не может быть пустым.')
        changed = []
        if direction is not None and who.kind != 'lead' and direction != task.direction:
            if direction and direction not in DIRECTIONS:
                raise TaskError('Неизвестное направление.')
            if direction and task.assignments.filter(is_active=True).exclude(role=direction).exists():
                raise TaskError('У задачи есть исполнители другого направления — сначала снимите их.')
            changed.append(f'направление: {DIRECTIONS.get(direction, "общая")}')
            task.direction = direction
        for field, value in (('title', title), ('description', (description or '').strip()),
                             ('priority', priority if priority in TaskPriority.values else task.priority),
                             ('deadline', deadline)):
            if getattr(task, field) != value:
                changed.append(str(Task._meta.get_field(field).verbose_name).lower())
                setattr(task, field, value)
        if changed:
            task.save()
            event(task, TaskEvent.Kind.EDITED, user, text=', '.join(changed))
    return task


def archive_task(task, user):
    with transaction.atomic():
        task = _lock(task)
        who = _require(user, task.project, task)
        if who.kind == 'lead':
            raise PermissionDenied('Удалять задачи может руководитель или ПМ проекта.')
        if task.is_archived:
            return task
        task.archive()  # физически не удаляем: исполнители, сдачи и история остаются
        event(task, TaskEvent.Kind.ARCHIVED, user)
    return task


def set_status(task, status, user) -> bool:
    """Ручной переход по кнопке. Возвращает False, если статус уже такой."""
    if status not in BOARD_STATUSES:
        raise TaskError('Неизвестный статус.')
    with transaction.atomic():
        task = _lock(task)
        _require(user, task.project, task)
        if task.is_archived:
            raise TaskError('Задача в архиве.')
        if status == task.status:
            return False
        active = list(task.assignments.select_for_update().filter(is_active=True))
        allowed = (ASSIGNED_TRANSITIONS if task.assignments.exists() else TRANSITIONS).get(task.status, set())
        if status not in allowed:
            if active and status in (TaskStatus.REVIEW, TaskStatus.DONE):
                raise TaskError('«На проверке» и «Готово» ставятся сдачей работы и проверкой результата.')
            raise TaskError(f'Нельзя перевести из «{task.get_status_display()}» в «{TaskStatus(status).label}».')
        for a in active:
            if a.status in (TaskStatus.NEW, TaskStatus.IN_PROGRESS) and a.status != status:
                a.status = status
                a.save(update_fields=['status', 'updated_at'])
        _set_status(task, status, user)
        _touch(task.project)
    return True


# ------------------------------------------------------- исполнители

def eligible_members(task, who: Actor | None = None):
    """Активные участники проекта, которых можно назначить на задачу."""
    from apps.teams.models import TeamMember, TeamRole

    qs = (TeamMember.objects.filter(project_id=task.project_id, status=TeamMember.Status.ACTIVE,
                                    intern__isnull=False, intern__is_archived=False)
          .exclude(role__in=[TeamRole.TEAM_LEAD]).select_related('intern'))
    if task.direction:
        qs = qs.filter(role=task.direction)
    if who is not None and who.kind == 'lead':
        qs = qs.filter(role=who.direction)
    return qs.order_by('intern__full_name')


def assign(task, intern, user):
    """Назначить исполнителя. Повторное назначение не создаёт дубль."""
    with transaction.atomic():
        task = _lock(task)
        who = _require(user, task.project, task)
        if task.is_archived:
            raise TaskError('Задача в архиве.')
        member = eligible_members(task, who).filter(intern=intern).first()
        if member is None:
            raise TaskError(f'{intern.full_name} не может получить эту задачу: нужен активный участник проекта '
                            f'{"направления " + DIRECTIONS.get(task.direction, task.direction) if task.direction else ""}'
                            .strip() + '.')
        existing = task.assignments.filter(intern=intern, is_active=True).first()
        if existing:
            return existing, False
        assignment = TaskAssignment.objects.create(
            task=task, intern=intern, person_name=intern.full_name, role=member.role, assigned_by=user,
        )
        if task.needs_reassign:
            task.needs_reassign = False
            task.save(update_fields=['needs_reassign', 'updated_at'])
        event(task, TaskEvent.Kind.ASSIGNED, user, assignment, new=intern.full_name)
        recompute(task, user)
        transaction.on_commit(lambda: _notify('task_assigned', assignment))
    return assignment, True


def _notify(name, obj):
    """Уведомления в Telegram (этап 8B) — через очередь бота, после коммита."""
    from apps.tgbot import notify

    getattr(notify, name)(obj)


def unassign(assignment, user):
    with transaction.atomic():
        task = _lock(assignment.task)
        _require(user, task.project, task)
        assignment = TaskAssignment.objects.select_for_update().get(pk=assignment.pk)
        if not assignment.is_active:
            raise TaskError('Исполнитель уже снят.')
        if assignment.status == TaskStatus.DONE:
            raise TaskError('Результат уже принят — снимать нечего, он остаётся в истории.')
        _close(assignment, 'снят(а) с задачи')
        event(task, TaskEvent.Kind.UNASSIGNED, user, assignment, old=assignment.person_name)
        recompute(task, user)
    return assignment


def _close(assignment, reason):
    assignment.is_active = False
    assignment.released_at = timezone.now()
    assignment.release_reason = reason[:255]
    assignment.save(update_fields=['is_active', 'released_at', 'release_reason', 'updated_at'])
    assignment.submissions.filter(outcome=TaskSubmission.Outcome.PENDING).update(
        outcome=TaskSubmission.Outcome.WITHDRAWN, reviewed_at=timezone.now())


def release_stale(*, intern_ids=None, project_ids=None, reason='', user=None) -> int:
    """Закрыть незавершённые назначения тех, кто больше не активный участник проекта.

    Общая точка для заморозки, отчисления, отмены, повышения, увольнения,
    снятия с проекта и завершения проекта. Принятые результаты остаются
    активными (это история выполненной работы), назначения других
    исполнителей не трогаются. Задача возвращается в очередь
    (needs_reassign) — до нового назначения «Готово» не ставится."""
    from apps.teams.models import TeamMember

    still = TeamMember.objects.filter(project_id=OuterRef('task__project_id'), intern_id=OuterRef('intern_id'),
                                      status=TeamMember.Status.ACTIVE)
    qs = (TaskAssignment.objects.filter(is_active=True, intern__isnull=False)
          .exclude(status=TaskStatus.DONE).annotate(still=Exists(still)).filter(still=False))
    if intern_ids is not None:
        qs = qs.filter(intern_id__in=list(intern_ids))
    if project_ids is not None:
        qs = qs.filter(task__project_id__in=list(project_ids))
    count = 0
    with transaction.atomic():
        for task_id in sorted(set(qs.values_list('task_id', flat=True))):
            task = Task.objects.select_for_update().get(pk=task_id)
            for assignment in qs.filter(task_id=task_id).select_for_update(of=('self',)):
                _close(assignment, reason or 'вышел(а) из проекта')
                event(task, TaskEvent.Kind.RELEASED, user, assignment, old=assignment.person_name,
                      text=reason)
                count += 1
            task.needs_reassign = True
            task.save(update_fields=['needs_reassign', 'updated_at'])
            recompute(task, user, text='исполнитель вышел из проекта')
        # release_stale — общая точка выхода из проекта: сверяем и Telegram-группы (8B)
        from apps.tgbot import groups

        transaction.on_commit(lambda: groups.reconcile(
            project_ids=list(project_ids) if project_ids is not None else None,
            intern_ids=list(intern_ids) if intern_ids is not None else None))
    return count


# ----------------------------------------------------- сдача работы

def validate_screenshot(file):
    from django.conf import settings

    if not file or not getattr(file, 'size', 0):
        raise TaskError('Пришлите скриншот результата — без него работа не принимается.')
    extension = os.path.splitext(file.name or '')[1].lstrip('.').lower()
    if extension not in SCREENSHOT_EXTENSIONS:
        raise TaskError(f'Скриншот должен быть изображением ({", ".join(SCREENSHOT_EXTENSIONS)}).')
    if file.size > settings.FILE_UPLOAD_MAX_MEMORY_SIZE:
        raise TaskError('Файл больше 20 МБ.')
    return file


def _clean_links(links) -> str:
    from django.core.validators import URLValidator

    items = [part.strip() for part in str(links or '').replace(',', '\n').split() if part.strip()]
    for item in items:
        try:
            URLValidator(schemes=['http', 'https'])(item)
        except ValidationError:
            raise TaskError(f'Неверная ссылка: {item[:80]}') from None
    return '\n'.join(items[:10])


def start_work(task_id, intern):
    """Исполнитель взял задачу в работу (кнопка бота)."""
    with transaction.atomic():
        task = Task.objects.select_for_update(of=('self',)).select_related('project').get(pk=task_id)
        assignment = task.assignments.select_for_update().filter(intern=intern, is_active=True).first()
        if assignment is None or task.is_archived or task.project.status not in WRITABLE_PROJECT:
            raise TaskError('Эта задача вам не назначена.')
        if assignment.status != TaskStatus.NEW:
            return assignment, False
        assignment.status = TaskStatus.IN_PROGRESS
        assignment.save(update_fields=['status', 'updated_at'])
        event(task, TaskEvent.Kind.STATUS, None, assignment, old=TaskStatus.NEW.label,
              new=TaskStatus.IN_PROGRESS.label, text=f'{assignment.person_name} взял(а) в работу')
        recompute(task)
    return assignment, True


def submit_work(task_id, intern, *, screenshot, links='', comment='', source=TaskSubmission.Source.TELEGRAM,
                user=None) -> TaskSubmission:
    """Сдача работы исполнителем. Повторная сдача до проверки — отказ, не дубль."""
    from apps.teams.models import TeamMember

    validate_screenshot(screenshot)
    links = _clean_links(links)
    with transaction.atomic():
        try:
            task = Task.objects.select_for_update(of=('self',)).select_related('project').get(pk=task_id)
        except Task.DoesNotExist:
            raise TaskError('Задача не найдена.') from None
        if task.is_archived or task.project_id is None or task.project.status not in WRITABLE_PROJECT:
            raise TaskError('Задача закрыта — сдавать работу нельзя.')
        assignment = task.assignments.select_for_update().filter(intern=intern, is_active=True).first()
        if assignment is None or not TeamMember.objects.filter(
                project=task.project, intern=intern, status=TeamMember.Status.ACTIVE).exists():
            raise TaskError('Эта задача вам не назначена.')
        if assignment.status == TaskStatus.REVIEW:
            raise TaskError('Работа уже на проверке — дождитесь решения тимлида.')
        if assignment.status == TaskStatus.DONE:
            raise TaskError('Работа уже принята.')
        submission = TaskSubmission.objects.create(
            task=task, assignment=assignment, attempt=assignment.submissions.count() + 1,
            screenshot=screenshot, links=links, comment=(comment or '').strip()[:4000], source=source,
        )
        assignment.status = TaskStatus.REVIEW
        assignment.save(update_fields=['status', 'updated_at'])
        event(task, TaskEvent.Kind.SUBMITTED, user, assignment, new=f'попытка {submission.attempt}',
              text=submission.comment)
        recompute(task, user)
        _touch(task.project)
        transaction.on_commit(lambda: _notify('submission_for_review', submission))
    return submission


def submit_from_telegram(chat_id, task_id, **kwargs) -> TaskSubmission:
    """Точка входа для бота (этап 8): исполнитель — по подтверждённому chat id."""
    from apps.interns.models import Intern

    intern = Intern.objects.filter(telegram_chat_id=chat_id, is_archived=False).first() if chat_id else None
    if intern is None:
        raise TaskError('Telegram не привязан к профилю стажёра.')
    return submit_work(task_id, intern, source=TaskSubmission.Source.TELEGRAM, **kwargs)


def can_review(who: Actor | None, task, assignment) -> bool:
    if who is None or not _writable(task.project, who):
        return False
    if who.kind in ('head', 'pm'):
        return True
    return bool(who.direction) and who.direction in (task.direction, assignment.role) and \
        task.direction in ('', who.direction)


def review(submission, user, *, approve: bool, comment='') -> TaskSubmission:
    """Ручная проверка: принять или вернуть на доработку (с причиной)."""
    comment = (comment or '').strip()
    if not approve and not comment:
        raise TaskError('Укажите, что нужно доработать.')
    with transaction.atomic():
        task = _lock(submission.task)
        submission = TaskSubmission.objects.select_for_update(of=('self',)).select_related('assignment').get(
            pk=submission.pk)
        assignment = submission.assignment
        if not can_review(actor(user, task.project), task, assignment):
            raise PermissionDenied('Проверяет тимлид направления, ПМ проекта или руководитель.')
        if submission.outcome != TaskSubmission.Outcome.PENDING:
            raise TaskError('Эта сдача уже проверена.')
        now = timezone.now()
        submission.outcome = TaskSubmission.Outcome.APPROVED if approve else TaskSubmission.Outcome.RETURNED
        submission.reviewed_by, submission.reviewed_at, submission.review_comment = user, now, comment
        submission.save(update_fields=['outcome', 'reviewed_by', 'reviewed_at', 'review_comment', 'updated_at'])
        if approve:
            assignment.status, assignment.approved_by, assignment.approved_at = TaskStatus.DONE, user, now
        else:
            assignment.status = TaskStatus.IN_PROGRESS
        assignment.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
        event(task, TaskEvent.Kind.APPROVED if approve else TaskEvent.Kind.RETURNED, user, assignment,
              new=f'попытка {submission.attempt}', text=comment)
        recompute(task, user)
        _touch(task.project)
        transaction.on_commit(lambda: _notify('submission_reviewed', submission))
    return submission


def add_comment(task, user, text):
    from apps.tasks.models import TaskComment

    text = (text or '').strip()
    if not text:
        raise TaskError('Комментарий пустой.')
    if actor(user, task.project) is None:
        raise PermissionDenied('Нет доступа к задаче.')
    comment = TaskComment.objects.create(task=task, author=user, text=text[:4000])
    event(task, TaskEvent.Kind.COMMENT, user, text=comment.text[:500])
    return comment


def add_attachment(task, user, file):
    from apps.documents.forms import validate_upload_file
    from apps.tasks.models import TaskAttachment

    with transaction.atomic():
        task = _lock(task)
        _require(user, task.project, task)
        try:
            validate_upload_file(file)
        except ValidationError as error:
            raise TaskError(error.messages) from None
        item = TaskAttachment.objects.create(task=task, file=file, name=os.path.basename(file.name)[:255],
                                             uploaded_by=user)
        event(task, TaskEvent.Kind.ATTACHMENT, user, new=item.name)
    return item


# ------------------------------------------------- для Telegram-бота

def intern_tasks(intern):
    """Назначенные стажёру задачи активных проектов — для бота (этап 8)."""
    return (TaskAssignment.objects.filter(intern=intern, is_active=True, task__is_archived=False,
                                          task__project__status__in=WRITABLE_PROJECT)
            .select_related('task', 'task__project').order_by('status', 'task__deadline', 'pk'))

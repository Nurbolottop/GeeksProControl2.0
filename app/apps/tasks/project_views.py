"""Задачи проекта: Kanban во вкладке «Задачи» и карточка задачи — у
руководителя, ПМ и тимлида (UI-03C, этап 7A).

Проект ищется в зоне роли (scoped_project, как у документов): чужой
проект — 404. Все права и переходы проверяет apps.tasks.workflow; здесь —
только отображение и разбор форм. Перетаскивания нет: состояние меняется
кнопками с серверной проверкой.
"""
from dataclasses import dataclass

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.documents.project_views import scoped_project
from apps.tasks import workflow
from apps.tasks.models import (
    BOARD_STATUSES, DIRECTION_CHOICES, Task, TaskAssignment, TaskPriority, TaskStatus, TaskSubmission,
)

PREFIX = {'head': 'projects', 'pm': 'pm_portal', 'lead': 'lead_portal'}
DETAIL = {'head': 'projects:detail', 'pm': 'pm_portal:project_detail', 'lead': 'lead_portal:project_detail'}
STATUS_TONES = {TaskStatus.NEW: 'neutral', TaskStatus.IN_PROGRESS: 'primary', TaskStatus.REVIEW: 'warning',
                TaskStatus.DONE: 'success', TaskStatus.CANCELLED: 'neutral'}
PRIORITY_TONES = {TaskPriority.CRITICAL: 'danger', TaskPriority.HIGH: 'warning'}
ACTION_LABELS = {
    (TaskStatus.NEW, TaskStatus.IN_PROGRESS): 'В работу',
    (TaskStatus.IN_PROGRESS, TaskStatus.NEW): 'В очередь',
    (TaskStatus.IN_PROGRESS, TaskStatus.REVIEW): 'На проверку',
    (TaskStatus.REVIEW, TaskStatus.IN_PROGRESS): 'Вернуть в работу',
    (TaskStatus.REVIEW, TaskStatus.DONE): 'Готово',
    (TaskStatus.DONE, TaskStatus.IN_PROGRESS): 'Переоткрыть',
}
DIRECTIONS = dict(DIRECTION_CHOICES)


@dataclass(frozen=True)
class TaskRights:
    edit: bool = False
    delete: bool = False


def rights(user, project, scope) -> TaskRights:
    who = workflow.actor(user, project)
    if who is None or (scope == 'head') != who.is_head or not workflow._writable(project, who):
        return TaskRights()
    return TaskRights(edit=who.kind != 'lead' or bool(who.direction), delete=who.kind != 'lead')


def tab_url(project, scope):
    return f"{reverse(DETAIL[scope], args=[project.pk])}?tab=tasks"


def task_url(project, task, scope):
    return reverse(f'{PREFIX[scope]}:task_detail', args=[project.pk, task.pk])


def _who(request, project, scope):
    who = workflow.actor(request.user, project)
    return who if who is not None and (scope == 'head') == who.is_head else None


def _actions(task, who):
    if not workflow.can_manage(who, task) or task.is_archived:
        return []
    assigned = getattr(task, 'ui_has_assignments', None)
    if assigned is None:
        assigned = task.assignments.exists()
    table = workflow.ASSIGNED_TRANSITIONS if assigned else workflow.TRANSITIONS
    return [(status, ACTION_LABELS.get((task.status, status), TaskStatus(status).label))
            for status in sorted(table.get(task.status, set()), key=BOARD_STATUSES.index)]


def _decorate(task, project, scope, who):
    prefix = PREFIX[scope]
    task.ui_status_tone = STATUS_TONES.get(task.status, 'neutral')
    task.ui_priority_tone = PRIORITY_TONES.get(task.priority, 'neutral')
    task.ui_direction = DIRECTIONS.get(task.direction, '')
    task.ui_url = task_url(project, task, scope)
    task.ui_status_url = reverse(f'{prefix}:task_status', args=[project.pk, task.pk])
    task.ui_actions = _actions(task, who)
    task.ui_can_manage = workflow.can_manage(who, task)
    return task


def tab_context(request, project, scope) -> dict:
    who = _who(request, project, scope)
    allowed = rights(request.user, project, scope)
    params = request.GET
    active_assignments = Prefetch('assignments', queryset=TaskAssignment.objects.filter(is_active=True),
                                  to_attr='ui_assignees')
    base = project.tasks.active().exclude(status=TaskStatus.CANCELLED)
    tasks = (base.select_related('author').prefetch_related(active_assignments)
             .annotate(ui_assignment_count=Count('assignments', distinct=True),
                       ui_pending=Count('submissions', filter=Q(submissions__outcome='pending'), distinct=True),
                       ui_files=Count('attachments', distinct=True))
             .order_by('deadline', '-created_at'))
    direction = params.get('direction', '')
    if direction == 'none':
        tasks = tasks.filter(direction='')
    elif direction in DIRECTIONS:
        tasks = tasks.filter(direction=direction)
    assignee = params.get('assignee', '')
    if assignee.isdigit():
        tasks = tasks.filter(assignments__intern_id=int(assignee), assignments__is_active=True)
    status = params.get('task_status', '')
    if status in BOARD_STATUSES:
        tasks = tasks.filter(status=status)
    columns = {key: [] for key in BOARD_STATUSES}
    for task in tasks:
        task.ui_has_assignments = task.ui_assignment_count > 0
        columns[task.status].append(_decorate(task, project, scope, who))
    counts = {key: 0 for key in BOARD_STATUSES}
    for key in base.values_list('status', flat=True):
        counts[key] = counts.get(key, 0) + 1
    people = (TaskAssignment.objects.filter(task__project=project, task__is_archived=False, is_active=True,
                                            intern__isnull=False)
              .values_list('intern_id', 'person_name').distinct().order_by('person_name'))
    opened = sum(counts[k] for k in (TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW))
    lead_direction = who.direction if who and who.kind == 'lead' else ''
    return {
        'task_columns': [{'key': key, 'label': TaskStatus(key).label, 'tone': STATUS_TONES[key],
                          'tasks': columns[key], 'count': len(columns[key]),
                          'count_label': str(len(columns[key]))} for key in BOARD_STATUSES],
        'tasks': [t for key in BOARD_STATUSES for t in columns[key]],
        'task_rights': allowed, 'task_statuses': [(k, TaskStatus(k).label) for k in BOARD_STATUSES],
        'task_priorities': TaskPriority.choices, 'task_filter': status,
        'task_directions': DIRECTION_CHOICES, 'task_direction': direction,
        'task_people': list(people), 'task_assignee': assignee,
        'task_filtered': bool(status or direction or assignee),
        'task_lead_direction': DIRECTIONS.get(lead_direction, ''),
        'task_create_url': reverse(f'{PREFIX[scope]}:task_create', args=[project.pk]),
        'task_counts': [(key, TaskStatus(key).label, counts.get(key, 0)) for key in BOARD_STATUSES],
        'task_open': opened, 'task_open_label': f'открыто: {opened}',
        'task_pending': (pending := sum(t.ui_pending for t in tasks_all(columns))),
        'task_pending_label': f'Ждут проверки: {pending}',
    }


def tasks_all(columns):
    return [t for items in columns.values() for t in items]


def _project_task(request, pk, task_pk, scope):
    project = scoped_project(request.user, pk, scope)
    return project, get_object_or_404(Task.objects.active(), pk=task_pk, project=project)


def _date(raw):
    import datetime

    try:
        return datetime.date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _flash(request, error):
    messages.error(request, ' '.join(getattr(error, 'messages', None) or [str(error)]))


def _back(request, project, task, scope):
    if request.POST.get('next') == 'task' and task is not None:
        return redirect(task_url(project, task, scope))
    return redirect(tab_url(project, scope))


def _guard(scope, request, project):
    """Зона роли и сервис должны совпадать: руководитель — только в /projects/."""
    if _who(request, project, scope) is None:
        raise PermissionDenied('Нет прав на задачи этого проекта.')


@login_required
@require_POST
def task_create(request, pk, scope='head'):
    from apps.interns.models import Intern

    project = scoped_project(request.user, pk, scope)
    try:
        _guard(scope, request, project)
        ids = [int(x) for x in request.POST.getlist('assignees') if x.isdigit()]
        task = workflow.create_task(
            project, request.user, title=request.POST.get('title'), description=request.POST.get('description'),
            direction=request.POST.get('direction', ''), priority=request.POST.get('priority', 'medium'),
            deadline=_date(request.POST.get('deadline')), assignees=list(Intern.objects.filter(pk__in=ids)),
        )
    except (workflow.TaskError, PermissionDenied) as error:
        _flash(request, error)
        return redirect(tab_url(project, scope))
    messages.success(request, f'Задача «{task.title}» создана.')
    return redirect(tab_url(project, scope))


@login_required
@require_POST
def task_update(request, pk, task_pk, scope='head'):
    project, task = _project_task(request, pk, task_pk, scope)
    try:
        _guard(scope, request, project)
        workflow.update_task(task, request.user, title=request.POST.get('title'),
                             description=request.POST.get('description'), priority=request.POST.get('priority'),
                             deadline=_date(request.POST.get('deadline')),
                             direction=request.POST.get('direction') if 'direction' in request.POST else None)
    except (workflow.TaskError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, 'Задача обновлена.')
    return _back(request, project, task, scope)


@login_required
@require_POST
def task_status(request, pk, task_pk, scope='head'):
    project, task = _project_task(request, pk, task_pk, scope)
    status = request.POST.get('status')
    try:
        _guard(scope, request, project)
        changed = workflow.set_status(task, status, request.user)
    except (workflow.TaskError, PermissionDenied) as error:
        _flash(request, error)
    else:
        if changed:
            messages.success(request, f'«{task.title}»: {TaskStatus(status).label.lower()}.')
    return _back(request, project, task, scope)


@login_required
@require_POST
def task_delete(request, pk, task_pk, scope='head'):
    project, task = _project_task(request, pk, task_pk, scope)
    try:
        _guard(scope, request, project)
        workflow.archive_task(task, request.user)
    except (workflow.TaskError, PermissionDenied) as error:
        _flash(request, error)
        return _back(request, project, task, scope)
    messages.success(request, f'Задача «{task.title}» перенесена в архив.')
    return redirect(tab_url(project, scope))


@login_required
def task_detail(request, pk, task_pk, scope='head'):
    project, task = _project_task(request, pk, task_pk, scope)
    who = _who(request, project, scope)
    _decorate(task, project, scope, who)
    assignments = list(task.assignments.select_related('intern', 'assigned_by', 'approved_by')
                       .annotate(ui_attempts=Count('submissions')))
    submissions = list(task.submissions.select_related('assignment', 'reviewed_by'))
    for item in submissions:
        item.ui_can_review = item.outcome == TaskSubmission.Outcome.PENDING and \
            workflow.can_review(who, task, item.assignment)
        item.ui_modal = f'return-{item.pk}'
        item.ui_form = f'return-form-{item.pk}'
        item.ui_links = [link for link in item.links.split('\n') if link]
        item.ui_tone = {'pending': 'warning', 'approved': 'success', 'returned': 'danger'}.get(item.outcome, 'neutral')
    prefix = PREFIX[scope]
    can_manage = workflow.can_manage(who, task)
    for a in assignments:
        a.ui_tone = STATUS_TONES.get(a.status, 'neutral') if a.is_active else 'neutral'
        a.ui_unassign_url = reverse(f'{prefix}:task_unassign', args=[project.pk, task.pk, a.pk]) \
            if can_manage and a.is_active and a.status != TaskStatus.DONE else ''
    candidates = list(workflow.eligible_members(task, who).exclude(
        intern_id__in=[a.intern_id for a in assignments if a.is_active])) if can_manage else []
    return render(request, 'tasks/project_task.html', {
        'project': project, 'task': task, 'scope': scope, 'assignments': assignments,
        'submissions': submissions, 'candidates': candidates, 'can_manage': can_manage,
        'can_delete': can_manage and who.kind != 'lead',
        'can_comment': who is not None,
        'events': task.events.select_related('user', 'assignment')[:100],
        'comments': task.comments.select_related('author'),
        'attachments': task.attachments.select_related('uploaded_by'),
        'priorities': TaskPriority.choices, 'directions': DIRECTION_CHOICES,
        'urls': {name: reverse(f'{prefix}:task_{name}', args=[project.pk, task.pk])
                 for name in ('update', 'status', 'delete', 'assign', 'comment', 'attach')},
        'review_url_name': f'{prefix}:task_review',
        'back_url': tab_url(project, scope), 'today': timezone.localdate(),
        'crumbs': [{'label': 'Проекты', 'href': reverse('projects:list') if scope == 'head' else
                    reverse(f'{prefix}:dashboard')},
                   {'label': project.name, 'href': reverse(DETAIL[scope], args=[project.pk])},
                   {'label': 'Задачи', 'href': tab_url(project, scope)},
                   {'label': task.title, 'href': ''}],
    })


@login_required
@require_POST
def task_assign(request, pk, task_pk, scope='head'):
    from apps.interns.models import Intern

    project, task = _project_task(request, pk, task_pk, scope)
    intern = Intern.objects.filter(pk=request.POST.get('intern') or 0).first()
    try:
        _guard(scope, request, project)
        if intern is None:
            raise workflow.TaskError('Выберите исполнителя.')
        _assignment, made = workflow.assign(task, intern, request.user)
    except (workflow.TaskError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, f'{intern.full_name} — исполнитель.' if made else f'{intern.full_name} уже исполнитель.')
    return redirect(task_url(project, task, scope))


@login_required
@require_POST
def task_unassign(request, pk, task_pk, assignment_pk, scope='head'):
    project, task = _project_task(request, pk, task_pk, scope)
    assignment = get_object_or_404(TaskAssignment, pk=assignment_pk, task=task)
    try:
        _guard(scope, request, project)
        workflow.unassign(assignment, request.user)
    except (workflow.TaskError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, f'{assignment.person_name} снят(а) с задачи.')
    return redirect(task_url(project, task, scope))


@login_required
@require_POST
def task_review(request, pk, task_pk, submission_pk, scope='head'):
    project, task = _project_task(request, pk, task_pk, scope)
    submission = get_object_or_404(TaskSubmission, pk=submission_pk, task=task)
    approve = request.POST.get('decision') == 'approve'
    try:
        _guard(scope, request, project)
        workflow.review(submission, request.user, approve=approve, comment=request.POST.get('comment', ''))
    except (workflow.TaskError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, f'{submission.assignment.person_name}: работа принята.' if approve
                         else f'{submission.assignment.person_name}: возвращено на доработку.')
    return redirect(task_url(project, task, scope))


@login_required
@require_POST
def task_comment(request, pk, task_pk, scope='head'):
    project, task = _project_task(request, pk, task_pk, scope)
    try:
        _guard(scope, request, project)
        workflow.add_comment(task, request.user, request.POST.get('text'))
    except (workflow.TaskError, PermissionDenied) as error:
        _flash(request, error)
    return redirect(f'{task_url(project, task, scope)}#comments')


@login_required
@require_POST
def task_attach(request, pk, task_pk, scope='head'):
    project, task = _project_task(request, pk, task_pk, scope)
    try:
        _guard(scope, request, project)
        if not request.FILES.get('file'):
            raise workflow.TaskError('Выберите файл.')
        workflow.add_attachment(task, request.user, request.FILES['file'])
    except (workflow.TaskError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, 'Материал добавлен.')
    return redirect(task_url(project, task, scope))


def urlpatterns_for(scope):
    from django.urls import path

    kw = {'scope': scope}
    return [
        path('<int:pk>/tasks/create/', task_create, kw, name='task_create'),
        path('<int:pk>/tasks/<int:task_pk>/', task_detail, kw, name='task_detail'),
        path('<int:pk>/tasks/<int:task_pk>/edit/', task_update, kw, name='task_update'),
        path('<int:pk>/tasks/<int:task_pk>/status/', task_status, kw, name='task_status'),
        path('<int:pk>/tasks/<int:task_pk>/delete/', task_delete, kw, name='task_delete'),
        path('<int:pk>/tasks/<int:task_pk>/assign/', task_assign, kw, name='task_assign'),
        path('<int:pk>/tasks/<int:task_pk>/assign/<int:assignment_pk>/remove/', task_unassign, kw,
             name='task_unassign'),
        path('<int:pk>/tasks/<int:task_pk>/review/<int:submission_pk>/', task_review, kw, name='task_review'),
        path('<int:pk>/tasks/<int:task_pk>/comment/', task_comment, kw, name='task_comment'),
        path('<int:pk>/tasks/<int:task_pk>/attach/', task_attach, kw, name='task_attach'),
    ]

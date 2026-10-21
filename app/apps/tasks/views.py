from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.projects.models import Project
from apps.tasks import selectors, services
from apps.tasks.forms import TaskAttachmentForm, TaskCommentForm, TaskForm
from apps.tasks.models import Task, TaskPriority, TaskStatus

User = get_user_model()


@login_required
def task_list(request):
    qs = selectors.filter_tasks(selectors.tasks_qs(), request.GET)
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page'))
    context = {
        'page': page,
        'params': request.GET,
        'projects': Project.objects.active().order_by('name'),
        'statuses': TaskStatus.choices,
        'priorities': TaskPriority.choices,
        'view': request.GET.get('view', ''),
        'today': timezone.localdate(),
    }
    return render(request, 'tasks/list.html', context)


@login_required
def task_kanban(request):
    qs = selectors.filter_tasks(selectors.tasks_qs(), request.GET)
    columns = [
        {
            'key': value, 'label': label,
            'tasks': [t for t in qs if t.status == value],
        }
        for value, label in TaskStatus.choices
        if value != TaskStatus.CANCELLED
    ]
    return render(request, 'tasks/kanban.html', {'columns': columns})


@login_required
def task_detail(request, pk):
    task = get_object_or_404(
        Task.objects.select_related('project', 'stage', 'assignee', 'author'),
        pk=pk,
    )
    comment_form = TaskCommentForm()
    attachment_form = TaskAttachmentForm()
    if request.method == 'POST':
        if 'comment' in request.POST:
            comment_form = TaskCommentForm(request.POST)
            if comment_form.is_valid():
                comment = comment_form.save(commit=False)
                comment.task = task
                comment.author = request.user
                comment.save()
                return redirect(task.get_absolute_url())
        elif 'attachment' in request.POST:
            attachment_form = TaskAttachmentForm(request.POST, request.FILES)
            if attachment_form.is_valid():
                attachment = attachment_form.save(commit=False)
                attachment.task = task
                attachment.uploaded_by = request.user
                attachment.name = attachment.file.name
                attachment.save()
                return redirect(task.get_absolute_url())
    context = {
        'task': task,
        'comments': task.comments.select_related('author'),
        'attachments': task.attachments.all(),
        'comment_form': comment_form,
        'attachment_form': attachment_form,
        'statuses': TaskStatus.choices,
    }
    return render(request, 'tasks/detail.html', context)


@login_required
def task_create(request):
    initial = {}
    if request.GET.get('project'):
        initial['project'] = request.GET['project']
    form = TaskForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        task = form.save(commit=False)
        task.author = request.user
        task.save()
        messages.success(request, f'Задача «{task.title}» создана.')
        if task.project_id:
            return redirect(f'{task.project.get_absolute_url()}?tab=tasks')
        return redirect('tasks:list')
    return render(
        request, 'tasks/form.html', {'form': form, 'title': 'Новая задача'},
    )


@login_required
def task_update(request, pk):
    task = get_object_or_404(Task, pk=pk)
    form = TaskForm(request.POST or None, instance=task)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Задача обновлена.')
        return redirect(task.get_absolute_url())
    return render(
        request, 'tasks/form.html',
        {'form': form, 'title': f'Редактирование: {task.title}', 'task': task},
    )


@login_required
def task_set_status(request, pk):
    """Быстрая смена статуса из списка/kanban/карточки проекта (HTMX)."""
    task = get_object_or_404(Task, pk=pk)
    if request.method == 'POST':
        status = request.POST.get('status')
        if status in TaskStatus.values and task.project_id and task.assignments.exists():
            # задача с исполнителями — только по правилам доски (этап 7A)
            from apps.tasks import workflow

            try:
                workflow.set_status(task, status, request.user)
            except (workflow.TaskError, workflow.PermissionDenied) as error:
                messages.error(request, ' '.join(getattr(error, 'messages', [str(error)])))
            task.refresh_from_db()
        elif status in TaskStatus.values:
            services.set_task_status(task, status, user=request.user)
    if request.htmx:
        return render(
            request, 'tasks/partials/task_row.html',
            {'task': task, 'statuses': TaskStatus.choices,
             'today': timezone.localdate()},
        )
    return redirect(request.META.get('HTTP_REFERER', task.get_absolute_url()))


@login_required
def task_delete(request, pk):
    """Удаление задачи. Возврат туда, откуда пришли."""
    task = get_object_or_404(Task, pk=pk)
    project = task.project
    if request.method == "POST":
        title = task.title
        task.archive()  # не удаляем физически: исполнители, сдачи и история остаются
        from apps.tasks.models import TaskEvent

        TaskEvent.objects.create(task=task, kind=TaskEvent.Kind.ARCHIVED, user=request.user)
        messages.success(request, f"Задача «{title}» удалена в архив.")
    if project:
        return redirect(f"{project.get_absolute_url()}?tab=tasks")
    return redirect("tasks:list")

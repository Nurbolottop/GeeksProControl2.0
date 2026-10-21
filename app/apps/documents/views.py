from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from apps.documents import services
from apps.documents.forms import DocumentTemplateForm, NewProjectBriefForm, ProjectBriefApplyForm
from apps.documents.models import (
    Document, DocumentStatus, DocumentTemplate, DocumentType, ProjectBrief,
    ProjectBriefLink,
)
from apps.projects.models import Project, ProjectStatus


@login_required
def document_list(request):
    services.ensure_default_types()
    qs = (
        Document.objects.active().filter(project__is_archived=False)
        .select_related('project', 'doc_type', 'uploaded_by').order_by('-created_at')
    )
    params = request.GET
    if params.get('project'):
        qs = qs.filter(project_id=params['project'])
    if params.get('type'):
        qs = qs.filter(doc_type_id=params['type'])
    if params.get('status'):
        qs = qs.filter(status=params['status'])
    paginator = Paginator(qs, 50)
    page = paginator.get_page(params.get('page'))
    context = {
        'page': page,
        'params': params,
        'projects': Project.objects.active().order_by('name'),
        'doc_types': DocumentType.objects.all(),
        'statuses': DocumentStatus.choices,
    }
    return render(request, 'documents/list.html', context)


def brief_apply(request, token):
    """Публичный бриф проекта — без входа в систему.

    Ссылка выпускается под конкретный проект (`ProjectBriefLink`), поэтому
    здесь не спрашиваем «какой проект» — он уже известен по токену.
    Повторная отправка по действующей ссылке правит тот же ``ProjectBrief``,
    а не создаёт новый.
    """
    link = ProjectBriefLink.objects.select_related('project').filter(
        token=token,
    ).first()
    if link is None or not link.is_open:
        return render(request, 'documents/brief_apply_expired.html', status=404)
    project = link.project
    if project.is_archived or project.status not in (ProjectStatus.ACTIVE, ProjectStatus.PAUSED):
        # Проект закрыт/отменён — бриф по нему больше не принимаем
        return render(request, 'documents/brief_apply_expired.html', status=404)
    existing = getattr(project, 'brief', None)
    form = ProjectBriefApplyForm(
        request.POST or None, request.FILES or None, instance=existing,
    )
    if request.method == 'POST' and form.is_valid():
        services.accept_brief_submission(link, form)
        return render(request, 'documents/brief_apply_done.html', {'project': project})
    return render(request, 'documents/brief_apply.html', {
        'form': form, 'project': project,
    })


BRIEF_RATE_LIMIT = 5          # отправок с одного адреса
BRIEF_RATE_WINDOW = 60 * 60   # за час


def _client_ip(request) -> str:
    return (request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR') or '').strip()


def _rate_limited(request) -> bool:
    from django.core.cache import cache

    key = f'brief-new:{_client_ip(request)}'
    if cache.add(key, 1, BRIEF_RATE_WINDOW):
        return False
    try:
        return cache.incr(key) > BRIEF_RATE_LIMIT
    except ValueError:
        return False


def new_project_brief(request):
    """Бриф на новый проект — постоянная ссылка без токена (UI-03B).

    Заказчик сам называет проект и оставляет контакты; проект с карточкой
    клиента заводится сразу, бриф привязывается к нему. Защита: скрытое
    поле-ловушка, лимит отправок с адреса, одна отправка на браузер,
    повторная отправка того же брифа нового проекта не заводит. Внутренних
    данных (команда, статусы, другие проекты) страница не показывает.
    """
    if request.session.get('brief_new_submitted'):
        return render(request, 'documents/brief_apply_done.html', {'project': None, 'brief': None})
    form = NewProjectBriefForm(request.POST or None, request.FILES or None)
    if request.method == 'POST':
        if request.POST.get('website'):
            # Бот: делаем вид, что всё приняли, и ничего не сохраняем
            return render(request, 'documents/brief_apply_done.html', {'project': None, 'brief': None})
        if _rate_limited(request):
            return render(request, 'documents/brief_apply_expired.html', {
                'message': 'Слишком много отправок с вашего адреса. Попробуйте позже.',
            }, status=429)
        if form.is_valid():
            brief, _created = services.accept_new_project_brief(form)
            request.session['brief_new_submitted'] = True
            return render(request, 'documents/brief_apply_done.html', {'project': None, 'brief': brief})
    return render(request, 'documents/brief_apply.html', {'form': form, 'project': None})


@login_required
def brief_requests(request):
    """Заявки с брифа: заказчик заполнил, проекта ещё нет."""
    requests_ = (
        ProjectBrief.objects.filter(project__isnull=True).order_by('-submitted_at')
    )
    briefs = (
        ProjectBrief.objects.filter(project__isnull=False, project__is_archived=False)
        .select_related('project__client').order_by('-submitted_at')[:30]
    )
    return render(request, 'documents/brief_requests.html', {
        'requests': requests_, 'briefs': briefs, 'title': 'Брифы заказчиков',
    })


@login_required
def brief_request_accept(request, pk):
    """Завести проект из заявки — вместе с карточкой клиента."""
    brief = get_object_or_404(ProjectBrief, pk=pk)
    if request.method == 'POST':
        project = services.project_from_brief(brief, user=request.user)
        messages.success(
            request, f'Проект «{project.name}» заведён из заявки — бриф уже внутри.',
        )
        return redirect(project.get_absolute_url())
    return redirect('documents:brief_requests')


@login_required
def template_list(request):
    """Шаблоны документов — не привязаны к проекту, второй раздел
    страницы «Документы» рядом со списком по проектам."""
    services.ensure_default_types()
    templates = DocumentTemplate.objects.select_related('doc_type', 'uploaded_by')
    return render(request, 'documents/template_list.html', {
        'templates': templates,
    })


@login_required
def template_create(request):
    services.ensure_default_types()
    form = DocumentTemplateForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        template = form.save(commit=False)
        template.uploaded_by = request.user
        template.save()
        messages.success(request, f'Шаблон «{template}» добавлен.')
        return redirect('documents:templates')
    return render(
        request, 'documents/template_form.html',
        {'form': form, 'title': 'Новый шаблон документа'},
    )


@login_required
def template_update(request, pk):
    template = get_object_or_404(DocumentTemplate, pk=pk)
    form = DocumentTemplateForm(
        request.POST or None, request.FILES or None, instance=template,
    )
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Шаблон обновлён.')
        return redirect('documents:templates')
    return render(
        request, 'documents/template_form.html',
        {'form': form, 'title': f'Редактирование шаблона: {template}'},
    )


@login_required
def template_delete(request, pk):
    template = get_object_or_404(DocumentTemplate, pk=pk)
    if request.method == 'POST':
        name = str(template)
        template.delete()
        messages.success(request, f'Шаблон «{name}» удалён.')
    return redirect('documents:templates')

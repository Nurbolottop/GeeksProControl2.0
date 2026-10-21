"""Документы проекта — одни и те же действия в трёх разделах (UI-03B).

Руководитель — /projects/…, ПМ — /pm/…, тимлид — /lead/… (у каждой роли
свой префикс URL, его держат middleware доступа). Проект сначала
ищется в зоне роли (чужой — 404), затем project_documents.rights решает,
что можно делать.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.documents import project_documents as docs
from apps.documents.models import Document
from apps.projects.models import Project


def scoped_project(user, pk, scope) -> Project:
    from apps.accounts.access import has_full_access

    if scope == 'pm':
        from apps.pm_portal.services import pm_project_or_404

        return pm_project_or_404(user, pk)
    if scope == 'lead':
        from apps.lead_portal.services import lead_project_or_404

        return lead_project_or_404(user, pk)
    if not has_full_access(user):
        raise Http404
    return get_object_or_404(Project.objects.active(), pk=pk)


def tab_url(project, scope) -> str:
    name = {'pm': 'pm_portal:project_detail', 'lead': 'lead_portal:project_detail'}.get(scope)
    base = reverse(name, args=[project.pk]) if name else project.get_absolute_url()
    return f'{base}?tab=documents'


def action_urls(project, scope) -> dict:
    """Адреса действий для шаблона вкладки — в зоне текущей роли."""
    prefix = {'pm': 'pm_portal', 'lead': 'lead_portal'}.get(scope, 'projects')
    return {
        'upload': reverse(f'{prefix}:document_upload', args=[project.pk]),
        'delete_name': f'{prefix}:document_delete',
        'brief_link': reverse(f'{prefix}:brief_link_create', args=[project.pk])
        if scope != 'head' else reverse('documents:brief_link_create', args=[project.pk]),
    }


def _errors(error) -> str:
    return ' '.join(getattr(error, 'messages', None) or [str(error)])


@login_required
@require_POST
def document_upload(request, pk, scope='head'):
    project = scoped_project(request.user, pk, scope)
    code = request.POST.get('doc_type', '')
    try:
        document = docs.upload(
            project, code, request.FILES.get('file'), request.user,
            title=request.POST.get('title', ''), comment=request.POST.get('comment', ''),
            document_date=_date(request.POST.get('document_date')), reason=request.POST.get('reason', ''),
        )
    except PermissionDenied as error:
        messages.error(request, _errors(error))
    except ValidationError as error:
        messages.error(request, _errors(error))
    else:
        messages.success(request, f'«{docs.LABELS[code]}» загружен: {document.title}.')
    return redirect(tab_url(project, scope))


@login_required
@require_POST
def document_delete(request, pk, document_pk, scope='head'):
    project = scoped_project(request.user, pk, scope)
    document = get_object_or_404(Document.objects.select_related('doc_type', 'project'), pk=document_pk, project=project)
    try:
        docs.delete(document, request.user, reason=request.POST.get('reason', ''))
    except PermissionDenied as error:
        messages.error(request, _errors(error))
    except ValidationError as error:
        messages.error(request, _errors(error))
    else:
        messages.success(request, f'«{docs.LABELS.get(document.doc_type.code, document.doc_type)}» удалён — версия осталась в истории.')
    return redirect(tab_url(project, scope))


@login_required
@require_POST
def brief_link_create(request, pk, scope='head'):
    from apps.documents.services import issue_brief_link

    project = scoped_project(request.user, pk, scope)
    if not docs.rights(request.user, project).edit:
        messages.error(request, 'Проект закрыт — ссылку на бриф не выпускаем.')
    else:
        issue_brief_link(project, user=request.user)
        messages.success(request, 'Ссылка на бриф создана — отправьте её заказчику.')
    return redirect(tab_url(project, scope))


def _date(value):
    import datetime

    try:
        return datetime.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def tab_context(request, project, scope) -> dict:
    """Контекст вкладки «Документы» (шаблон documents/_project_tab.html)."""
    from apps.documents.services import active_brief_link

    data = docs.overview(project, request.user)
    if not data['rights'].view:
        raise Http404
    return {
        'docs': data, 'doc_urls': action_urls(project, scope), 'doc_scope': scope,
        'brief_link': active_brief_link(project),
    }

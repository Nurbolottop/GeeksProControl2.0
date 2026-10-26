import datetime

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.projects.forms import (
    ProjectAboutForm,
    ProjectAccessForm,
    ProjectClientForm,
    ProjectCreateForm,
    ProjectDatesForm,
    ProjectDetailsForm,
    ProjectForm,
    ProjectLinksForm,
    ProjectProgressForm,
    StageExtendForm,
    StageUpdateForm,
)
from apps.projects.models import (
    Project,
    ProjectReport,
    ProjectAccess,
    ProjectStage,
    ProjectStageKey,
    ProjectStatus,
    ProjectStatusHistory,
    ProjectType,
)
from apps.flows.models import Flow
from apps.projects import selectors, services
from apps.teams.models import TeamRole


# Категории списка проектов — отдельные страницы (не query-фильтры)
PROJECT_CATEGORIES = {
    'all': {'title': 'Все проекты', 'statuses': None},
    'in_progress': {
        'title': 'Проекты в процессе',
        'statuses': [ProjectStatus.ACTIVE, ProjectStatus.PAUSED],
    },
    'rejected': {
        'title': 'Отклонённые проекты',
        'statuses': [ProjectStatus.CANCELLED, ProjectStatus.REFUSED],
    },
    'completed': {
        'title': 'Завершённые проекты',
        'statuses': [ProjectStatus.COMPLETED],
    },
}


def _attach_last_reports(projects) -> None:
    """Подтягивает последний написанный отчёт для каждого проекта на странице.

    Один запрос вместо N+1: берём все отчёты нужных проектов сразу,
    в Python оставляем по одному — самому свежему — на проект.
    """
    ids = [p.pk for p in projects]
    if not ids:
        return
    reports = (
        ProjectReport.objects.filter(project_id__in=ids)
        .order_by('project_id', '-date', '-created_at')
    )
    latest_by_project = {}
    for report in reports:
        latest_by_project.setdefault(report.project_id, report)
    for project in projects:
        project.last_report = latest_by_project.get(project.pk)


CATEGORY_TABS = [('all', 'Активные'), ('in_progress', 'В процессе'), ('rejected', 'Отклонённые'), ('completed', 'Завершённые')]
CATEGORY_URLS = {'all': 'projects:list', 'in_progress': 'projects:list_in_progress',
                 'rejected': 'projects:list_rejected', 'completed': 'projects:list_completed'}


def project_list_context(request, base_qs, detail_url: str, title: str, statuses=None, exclude_closed=False):
    """Общий список проектов 2.0 (руководитель, ПМ, тимлид — разные base_qs)."""
    from apps.projects import card

    scope = base_qs
    qs = card.list_queryset(base_qs)
    if statuses:
        qs = qs.filter(status__in=statuses)
    elif exclude_closed and not request.GET.get('status'):
        qs = qs.exclude(status__in=[ProjectStatus.COMPLETED, ProjectStatus.CANCELLED, ProjectStatus.REFUSED])
    qs = card.apply_filters(qs, request.GET)
    qs, sort = card.apply_sort(qs, request.GET.get('sort'))
    page = Paginator(qs, 20).get_page(request.GET.get('page'))
    card.decorate(page.object_list)
    _attach_last_reports(page.object_list)
    return {
        'page_obj': page, 'page': page, 'title': title, 'detail_url': detail_url, 'sort': sort,
        'options': card.filter_options(scope), 'params': request.GET,
        'has_filters': any(request.GET.get(k) for k in ('q', 'status', 'primary_stage', 'type', 'pm', 'lead')),
    }


@login_required
def project_list(request, category='all'):
    config = PROJECT_CATEGORIES[category]
    context = project_list_context(
        request, Project.objects.active(), 'projects:detail', config['title'],
        statuses=config['statuses'], exclude_closed=category == 'all',
    )
    context.update({
        'category': category,
        'category_tabs': [{'key': key, 'label': label, 'href': reverse(CATEGORY_URLS[key])} for key, label in CATEGORY_TABS],
        'show_status_filter': config['statuses'] is None,
        'can_create': True,
    })
    if request.htmx and request.htmx.target == 'project-list':
        # htmx меняет содержимое #project-list — отдаём только таблицу
        return render(request, 'projects/_project_table.html', context)
    return render(request, 'projects/list.html', context)


@login_required
def project_kanban(request):
    """Pipeline: проекты по этапам (ТЗ §6.4). Drag-and-drop через HTMX."""
    projects = selectors.annotate_deadline_statuses(
        selectors.active_projects().order_by('planned_end_date'),
    )
    columns = []
    for key, label in ProjectStageKey.choices:
        if key == ProjectStageKey.COMPLETED:
            continue
        columns.append({
            'key': key,
            'label': label,
            'projects': [p for p in projects if p.current_stage == key],
        })
    return render(request, 'projects/kanban.html', {'columns': columns})


@login_required
def project_move_stage(request, pk):
    """Endpoint для drag-and-drop в Kanban: двигает проект между этапами."""
    if request.method != 'POST':
        return redirect('projects:kanban')
    project = get_object_or_404(Project.objects.active(), pk=pk)
    stage_key = request.POST.get('stage')
    if stage_key in ProjectStageKey.values:
        services.move_project_to_stage(project, stage_key, user=request.user)
    return HttpResponse(status=204)


CARD_TABS = [('overview', 'Обзор'), ('stages', 'Этапы'), ('team', 'Команда'),
             ('tasks', 'Задачи'), ('documents', 'Документы'), ('report', 'Отчёты')]
EXTRA_TABS = [('access', 'Доступы'), ('graphics', 'Графика'), ('history', 'История')]
LEGACY_TABS = {'access', 'graphics', 'history'}


@login_required
def project_detail(request, pk):
    """Карточка проекта 2.0 (руководитель). Разделы общие с порталами ПМ
    и тимлида (apps.projects.portal); доступы, графика и история —
    переходные вкладки 1.0 только у руководителя."""
    from apps.projects import portal

    project = get_object_or_404(
        Project.objects.select_related('client', 'project_type', 'primary_stage', 'primary_stage_set_by',
                                       'recruitment_changed_by', 'closed_by'),
        pk=pk,
    )
    project.deadline_status = services.calculate_deadline_status(project)
    tab = portal.valid_tab('head', request.GET.get('tab', 'overview'), extra=dict(EXTRA_TABS))
    context = portal.card_context(request, project, 'head', tab)
    context.update({
        'extra_tabs': [{'key': k, 'label': l, 'href': f'?tab={k}'} for k, l in EXTRA_TABS],
        'is_legacy_tab': tab in LEGACY_TABS,
        'crumbs': [{'label': 'Главная', 'href': reverse('dashboard')},
                   {'label': 'Проекты', 'href': reverse('projects:list')}, {'label': project.name, 'href': ''}],
        # совместимость с переходными вкладками 1.0
        'can_manage': True,
    })
    if tab == 'access':
        context['accesses'] = project.accesses.all()
        context['access_form'] = ProjectAccessForm()
    elif tab == 'graphics':
        from apps.projects import graphics
        context.update(graphics.graphics_context(project))
    elif tab == 'history':
        context['history'] = project.history.select_related('user')[:50]
    return render(request, 'projects/card.html', context)


@login_required
@require_POST
def recruitment_set(request, pk):
    """Руководитель открывает / закрывает набор стажёров в проект."""
    project = get_object_or_404(Project.objects.active(), pk=pk)
    is_open = request.POST.get('open') == '1'
    try:
        services.set_recruitment(project, is_open, request.user)
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, 'Набор открыт.' if is_open else 'Набор закрыт.')
    return redirect(f'{project.get_absolute_url()}?tab={request.POST.get("tab") or "team"}')


@login_required
@require_POST
def stage_status_set(request, pk):
    """Статус этапа из вкладки «Этапы». Основной этап при этом не меняется."""
    stage = get_object_or_404(ProjectStage.objects.select_related('project'), pk=pk)
    status = request.POST.get('status')
    if status in ProjectStage.Status.values:
        stage.status = status
        services.update_stage(stage, user=request.user)
        messages.success(request, f'Этап «{stage.get_key_display()}»: {stage.get_status_display().lower()}.')
    return redirect(f'{stage.project.get_absolute_url()}?tab=stages')


@login_required
def project_create(request):
    """Новый проект. Заказчика можно завести здесь же, не уходя с формы.

    После создания сразу открываем документы проекта с готовой ссылкой на
    бриф: её нужно отправить заказчику в тот же день, а не искать потом.
    """
    from apps.clients.models import Client

    form = ProjectCreateForm(request.POST or None)
    if request.method == 'POST':
        data = request.POST.copy()
        changed = False

        # Заказчика можно завести прямо здесь, не уходя с формы;
        # поток и номер в потоке проставляет services.ensure_group
        new_client = data.get('new_client', '').strip()
        if new_client:
            client, created = Client.objects.get_or_create(
                organization=new_client,
                defaults={'city': data.get('city', '').strip()},
            )
            data['client'] = client.pk
            changed = True
            if created:
                messages.success(request, f'Заказчик «{client}» создан.')

        if changed:
            form = ProjectCreateForm(data)
        if form.is_valid():
            from apps.documents import services as doc_services

            project = form.save(commit=False)
            services.create_project(project, user=request.user)
            # Бриф нужен сразу: первое дело на новом проекте — отправить
            # ссылку заказчику, поэтому выпускаем её вместе с проектом
            link = doc_services.issue_brief_link(project, user=request.user)
            url = request.build_absolute_uri(link.get_absolute_url())
            messages.success(
                request,
                f'Проект «{project.name}» создан. Ссылка на бриф для заказчика: {url}',
            )
            return redirect(f'{project.get_absolute_url()}?tab=documents&brief=new')
    return render(
        request, 'projects/form.html',
        {'form': form, 'title': 'Новый проект'},
    )


@login_required
def project_update(request, pk):
    project = get_object_or_404(Project, pk=pk)
    old_values = {
        field: getattr(project, field) for field in services.TRACKED_FIELDS
    }
    form = ProjectForm(request.POST or None, instance=project)
    if request.method == 'POST' and form.is_valid():
        updated = form.save(commit=False)
        services.update_project(
            updated, old_values, user=request.user,
            reason=form.cleaned_data.get('change_reason', ''),
        )
        messages.success(request, 'Проект обновлён.')
        return redirect(updated.get_absolute_url())
    return render(
        request, 'projects/form.html',
        {'form': form, 'title': f'Редактирование: {project.name}', 'project': project},
    )


@login_required
def project_delete(request, pk):
    """Удаление проекта — только POST с подтверждением кодом проекта.

    Вместе с проектом каскадно удаляются его этапы, задачи, отчёты,
    документы, доступы и история. Факт удаления остаётся в журнале аудита.
    """
    from apps.audit.services import log as audit_log

    project = get_object_or_404(Project, pk=pk)
    if request.method != 'POST':
        return redirect(project.get_absolute_url())

    confirm = request.POST.get('confirm_code', '').strip()
    expected = project.display_code or str(project.pk)
    if confirm != expected:
        messages.error(
            request,
            f'Проект не удалён: для подтверждения введите его код «{expected}».',
        )
        return redirect(project.get_absolute_url())

    name = str(project)
    counts = (
        f'этапов {project.stages.count()}, задач {project.tasks.count()}, '
        f'отчётов {project.reports.count()}, документов {project.documents.count()}'
    )
    audit_log(
        project, 'deleted', old_value=counts,
        reason=request.POST.get('reason', ''), user=request.user,
    )
    project.delete()
    messages.success(request, f'Проект «{name}» удалён вместе со связанными данными.')
    return redirect('projects:list')


def closure_context(request, project) -> dict:
    """Готовность к закрытию и (после закрытия) итог: дата, кто, выпускники."""
    from apps.projects import closure

    state = closure.readiness(project)
    data = {'closure': state, 'can_close': closure.can_close(request.user),
            'closure_label': f'{state.done} из {len(state.items)}'}
    if project.status == ProjectStatus.COMPLETED:
        record = project.history.filter(field=closure.GRADUATES_FIELD).order_by('-created_at').first()
        names = record.new_value if record and record.new_value != 'нет' else ''
        data['closure_graduates'] = [name for name in names.split('\n') if name]
    else:
        data['closure_preview'] = _closure_preview(project)
    return data


def _closure_preview(project) -> dict:
    """Что произойдёт с участниками — для окна подтверждения."""
    from apps.dashboard.graduation import compute
    from apps.interns.models import InternStatus
    from apps.teams.models import TeamMember
    from apps.teams.selectors import lead_intern_ids

    members = list(project.team_members.filter(status=TeamMember.Status.ACTIVE, intern__isnull=False).select_related('intern'))
    leads = set(lead_intern_ids())
    graduates = compute({m.intern_id for m in members}, exclude_project_id=project.pk).ids
    staff = [m for m in members if m.role == TeamRole.TEAM_LEAD or m.intern_id in leads]
    interns = [m for m in members if m not in staff]
    frozen = [m for m in interns if m.intern.status == InternStatus.PAUSED]
    working = [m for m in interns if m not in frozen]
    return {
        'new': sorted({m.intern.full_name for m in working if m.intern_id not in graduates}),
        'old': sorted({m.intern.full_name for m in working if m.intern_id in graduates}),
        'frozen': sorted({m.intern.full_name for m in frozen}),
        'staff': sorted({m.intern.full_name for m in staff}),
    }


@login_required
@require_POST
def project_complete(request, pk):
    """«Завершить проект» — только руководитель, только через closure.close_project.

    Принудительного закрытия в 2.0 нет: не выполнены условия — проект не закрывается.
    """
    from apps.projects import closure

    project = get_object_or_404(Project.objects.active(), pk=pk)
    try:
        result = closure.close_project(project, request.user)
    except PermissionDenied as error:
        messages.error(request, str(error))
    except closure.ClosureError as error:
        messages.error(request, 'Проект не закрыт. ' + ' '.join(error.messages))
    else:
        messages.success(request, f'Проект «{project.name}» закрыт. Новых выпускников: {len(result.graduates)}.')
    return redirect(f'{project.get_absolute_url()}?tab=overview')


@login_required
@require_POST
def closure_confirm(request, pk, key):
    """Руководитель фиксирует условие закрытия (разработка / production / приёмка)."""
    from apps.projects import closure

    project = get_object_or_404(Project.objects.active(), pk=pk)
    try:
        closure.set_confirmation(project, key, request.POST.get('value') == '1', request.user)
    except PermissionDenied as error:
        messages.error(request, str(error))
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        label = closure.CONFIRMATIONS[key][2]
        messages.success(request, f'«{label}» — {"подтверждено" if request.POST.get("value") == "1" else "подтверждение снято"}.')
    return redirect(f'{project.get_absolute_url()}?tab=overview')


# Инлайн-редактирование панелей карточки проекта (AJAX через HTMX)
PROJECT_SECTIONS = {
    'progress': ProjectProgressForm,
    'client': ProjectClientForm,
    'links': ProjectLinksForm,
    'about': ProjectAboutForm,
    'details': ProjectDetailsForm,
    'dates': ProjectDatesForm,
}


@login_required
def project_section(request, pk, section):
    """Отдаёт панель обзора: в режиме просмотра или редактирования.

    GET  ?edit=1 — форма панели, GET — просмотр, POST — сохранение.
    Каждая панель редактируется отдельно, без общей формы проекта.
    """
    if section not in PROJECT_SECTIONS:
        raise Http404
    project = get_object_or_404(
        Project.objects.select_related(
            'client', 'project_type',
        ),
        pk=pk,
    )
    form_class = PROJECT_SECTIONS[section]
    view_template = f'projects/partials/section_{section}.html'
    form_template = f'projects/partials/section_{section}_form.html'

    if request.method == 'POST':
        old_values = {
            field: getattr(project, field) for field in services.TRACKED_FIELDS
        }
        form = form_class(request.POST, instance=project)
        if form.is_valid():
            updated = form.save(commit=False)
            services.update_project(
                updated, old_values, user=request.user,
                reason=form.cleaned_data.get('change_reason', ''),
            )
            if hasattr(form, 'save_client'):
                form.save_client()
            project.refresh_from_db()
            project.deadline_status = services.calculate_deadline_status(project)
            return render(request, view_template, {'project': project})
        return render(request, form_template, {'form': form, 'project': project})

    if request.GET.get('edit'):
        return render(
            request, form_template,
            {'form': form_class(instance=project), 'project': project},
        )
    project.deadline_status = services.calculate_deadline_status(project)
    return render(request, view_template, {'project': project})


@login_required
def access_create(request, pk):
    """Добавление доступа (логин/пароль) в карточку проекта."""
    project = get_object_or_404(Project, pk=pk)
    if request.method == 'POST':
        form = ProjectAccessForm(request.POST)
        if form.is_valid():
            access = form.save(commit=False)
            access.project = project
            access.save()
            ProjectStatusHistory.objects.create(
                project=project, field='Доступы',
                new_value=f'Добавлен доступ: {access.service}',
                user=request.user,
            )
            messages.success(request, f'Доступ «{access.service}» добавлен.')
        else:
            messages.error(request, 'Не удалось сохранить доступ — проверьте поля.')
    return redirect(f'{project.get_absolute_url()}?tab=access')


@login_required
def access_update(request, pk):
    access = get_object_or_404(
        ProjectAccess.objects.select_related('project'), pk=pk,
    )
    form = ProjectAccessForm(request.POST or None, instance=access)
    if request.method == 'POST' and form.is_valid():
        form.save()
        ProjectStatusHistory.objects.create(
            project=access.project, field='Доступы',
            new_value=f'Изменён доступ: {access.service}',
            user=request.user,
        )
        messages.success(request, 'Доступ обновлён.')
        return redirect(f'{access.project.get_absolute_url()}?tab=access')
    return render(
        request, 'projects/access_form.html',
        {'form': form, 'access': access, 'project': access.project,
         'title': f'Доступ: {access.service}'},
    )


@login_required
def access_delete(request, pk):
    access = get_object_or_404(
        ProjectAccess.objects.select_related('project'), pk=pk,
    )
    project = access.project
    if request.method == 'POST':
        ProjectStatusHistory.objects.create(
            project=project, field='Доступы',
            new_value=f'Удалён доступ: {access.service}',
            user=request.user,
        )
        access.delete()
        messages.success(request, 'Доступ удалён.')
    return redirect(f'{project.get_absolute_url()}?tab=access')


@login_required
def stage_update(request, pk):
    """Настройка этапа: статус, дедлайн, комментарий (AJAX)."""
    stage = get_object_or_404(
        ProjectStage.objects.select_related('project'), pk=pk,
    )
    if request.method == 'POST':
        form = StageUpdateForm(request.POST, instance=stage)
        if form.is_valid():
            services.update_stage(form.save(commit=False), user=request.user)
            return _stage_row(request, stage)
        return render(
            request, 'projects/partials/stage_form.html',
            {'form': form, 'stage': stage},
        )
    return render(
        request, 'projects/partials/stage_form.html',
        {'form': StageUpdateForm(instance=stage), 'stage': stage},
    )


@login_required
def stage_complete(request, pk):
    """Кнопка «Завершить» у этапа (AJAX)."""
    stage = get_object_or_404(
        ProjectStage.objects.select_related('project'), pk=pk,
    )
    if request.method == 'POST':
        services.complete_stage(stage, user=request.user)
    return _stage_row(request, stage)


@login_required
def stage_extend(request, pk):
    """Кнопка «Продлить дедлайн»: новая дата и обязательная причина (AJAX)."""
    stage = get_object_or_404(
        ProjectStage.objects.select_related('project'), pk=pk,
    )
    if request.method == 'POST':
        form = StageExtendForm(request.POST)
        if form.is_valid():
            services.extend_stage_deadline(
                stage, form.cleaned_data['deadline'],
                form.cleaned_data['reason'], user=request.user,
            )
            return _stage_row(request, stage)
        return render(
            request, 'projects/partials/stage_extend_form.html',
            {'form': form, 'stage': stage},
        )
    return render(
        request, 'projects/partials/stage_extend_form.html',
        {'form': StageExtendForm(initial={'deadline': stage.deadline}),
         'stage': stage},
    )


@login_required
def stage_row(request, pk):
    """Возврат строки этапа в режим просмотра (кнопка «Отмена»)."""
    stage = get_object_or_404(
        ProjectStage.objects.select_related('project'), pk=pk,
    )
    return _stage_row(request, stage)


def _stage_row(request, stage):
    stage.refresh_from_db()
    return render(
        request, 'projects/partials/stage_row.html',
        {'stage': stage, 'project': stage.project,
         'today': timezone.localdate()},
    )


@login_required
def report_list(request):
    """Отчёты по всем проектам сразу — что пишут ПМ, в одном списке."""
    qs = ProjectReport.objects.select_related('project', 'author')
    params = request.GET
    if params.get('project'):
        qs = qs.filter(project_id=params['project'])
    if params.get('q'):
        qs = qs.filter(text__icontains=params['q'])
    paginator = Paginator(qs, 50)
    page = paginator.get_page(params.get('page'))
    context = {
        'page': page,
        'params': params,
        'projects': Project.objects.active().order_by('name'),
    }
    return render(request, 'projects/report_list.html', context)


@login_required
def report_create(request, pk):
    """Новый отчёт по проекту."""
    project = get_object_or_404(Project, pk=pk)
    if request.method == 'POST':
        text = request.POST.get('text', '').strip()
        if not text:
            messages.error(request, 'Отчёт пустой — напишите текст.')
            return redirect(f'{project.get_absolute_url()}?tab=report')
        report = ProjectReport.objects.create(
            project=project, text=text, author=request.user,
        )
        ProjectStatusHistory.objects.create(
            project=project, field='Отчёт',
            new_value=f'Отчёт от {report.date:%d.%m.%Y}', user=request.user,
        )
        messages.success(request, f'Отчёт от {report.date:%d.%m.%Y} сохранён.')
    return redirect(f'{project.get_absolute_url()}?tab=report')


@login_required
def report_update(request, pk):
    """Правка отчёта по проекту."""
    report = get_object_or_404(
        ProjectReport.objects.select_related('project'), pk=pk,
    )
    project = report.project
    if request.method == 'POST':
        text = request.POST.get('text', '').strip()
        if text:
            report.text = text
            report.save(update_fields=['text', 'updated_at'])
            messages.success(request, 'Отчёт обновлён.')
        else:
            messages.error(request, 'Отчёт пустой — текст не сохранён.')
    return redirect(f'{project.get_absolute_url()}?tab=report')


@login_required
def report_delete(request, pk):
    """Удаление отчёта."""
    report = get_object_or_404(
        ProjectReport.objects.select_related('project'), pk=pk,
    )
    project = report.project
    if request.method == 'POST':
        date = report.date
        report.delete()
        messages.success(request, f'Отчёт от {date:%d.%m.%Y} удалён.')
    return redirect(f'{project.get_absolute_url()}?tab=report')


@login_required
@require_POST
def primary_stage_set(request, pk):
    """Руководитель выбирает этап проекта (вкладка «Этапы»)."""
    project = get_object_or_404(Project.objects.active(), pk=pk)
    stage = get_object_or_404(ProjectStage, pk=request.POST.get('stage') or 0, project=project)
    services.set_primary_stage(project, stage, request.user)
    messages.success(request, f'Этап: {stage.get_key_display()}.')
    return redirect(f'{project.get_absolute_url()}?tab=stages')

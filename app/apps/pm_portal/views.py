from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from apps.attendance import services as attendance_services
from apps.attendance.models import GroupMeeting
from apps.pm_portal import services, stages as stage_reminders
from apps.pm_portal.forms import PMClientForm, PMProjectTypeForm
from apps.projects.models import ProjectReport, ProjectStage
from apps.projects.forms import ProjectLinksForm
from apps.projects.services import calculate_deadline_status, sync_stages_to_type
from apps.teams import services as team_services
from apps.teams.forms import TeamMemberEditForm, TeamMemberForm
from apps.teams.models import TeamMember, TeamRole
from apps.teams.views import _role_from, _title_for, _with_new_person, people_options
from apps.training.models import Specialization


@login_required
def dashboard(request):
    """Список проектов, где текущий пользователь — активный ПМ."""
    from apps.projects.models import ProjectStatus

    category = request.GET.get('status', 'all')
    projects = services.pm_projects(request.user)
    if category == 'in_progress':
        projects = projects.filter(status__in=[ProjectStatus.ACTIVE, ProjectStatus.PAUSED])
    elif category == 'completed':
        projects = projects.filter(status=ProjectStatus.COMPLETED)
    elif category == 'cancelled':
        projects = projects.filter(status__in=[ProjectStatus.CANCELLED, ProjectStatus.REFUSED])
    else:
        category = 'all'
    from apps.projects.views import project_list_context

    base = projects
    projects = list(projects)
    for project in projects:
        project.stage_check_needed = stage_reminders.needs_stage_check(project)
    context = project_list_context(request, base, 'pm_portal:project_detail', 'Мои проекты')
    context.update({'projects': projects, 'category': category, 'category_tabs': PORTAL_TABS})
    if request.htmx and request.htmx.target == 'project-list':
        return render(request, 'projects/_project_table.html', context)
    return render(request, 'pm_portal/dashboard.html', context)


PORTAL_TABS = [{'key': k, 'label': l, 'href': f'?status={k}'} for k, l in (
    ('all', 'Все'), ('in_progress', 'В процессе'), ('completed', 'Завершённые'), ('cancelled', 'Отменённые'))]


PM_SECTIONS = {'type': PMProjectTypeForm, 'links': ProjectLinksForm}


@login_required
def project_edit(request, pk, section):
    """ПМ заполняет то, что знает только он: тип проекта и ссылки."""
    if section not in PM_SECTIONS:
        raise Http404
    project = services.pm_project_or_404(request.user, pk)
    form = PM_SECTIONS[section](request.POST or None, instance=project)
    if request.method == 'POST' and form.is_valid():
        changed_type = 'project_type' in form.changed_data
        form.save()
        if changed_type:
            # от типа зависит этап разработки: web → Frontend, мобильный → Mobile
            sync_stages_to_type(project)
        messages.success(request, 'Сохранено.')
        return redirect(f"{reverse('pm_portal:project_detail', args=[project.pk])}?tab=overview")
    return render(request, 'projects/portal/form_page.html', {
        'project': project, 'form': form, 'section': section,
        'title': 'Тип проекта' if section == 'type' else 'Ссылки проекта',
        'cancel_url': f"{reverse('pm_portal:project_detail', args=[project.pk])}?tab=overview",
    })


@login_required
def project_detail(request, pk):
    """Проект глазами ПМ (2.0, UI-03C): обзор, этапы и основной этап,
    команда и контакты, задачи, документы, отчёты, табель, заказчик.
    Закрытие проекта и набор — только у руководителя."""
    from apps.projects import portal

    project = services.pm_project_or_404(request.user, pk)
    project.deadline_status = calculate_deadline_status(project)
    tab = portal.valid_tab('pm', request.GET.get('tab', 'overview'))
    context = portal.card_context(request, project, 'pm', tab)
    context['stage_check_needed'] = stage_reminders.needs_stage_check(project)
    return render(request, 'projects/card.html', context)


def _stages_url(project):
    return f"{reverse('pm_portal:project_detail', args=[project.pk])}?tab=stages"


@login_required
def stage_set(request, pk, stage_pk):
    """ПМ меняет статус одного этапа: не начат / в процессе / завершён."""
    project = services.pm_project_or_404(request.user, pk)
    if request.method != 'POST':
        return redirect(_stages_url(project))
    stage = get_object_or_404(
        ProjectStage.objects.exclude(key='completed'), pk=stage_pk, project=project,
    )
    status = request.POST.get('status', '')
    if status not in dict(ProjectStage.Status.choices):
        messages.error(request, 'Выберите статус этапа из списка.')
        return redirect(_stages_url(project))
    if stage_reminders.set_stage_status(project, stage, status, user=request.user):
        messages.success(
            request,
            f'Этап «{stage.get_key_display()}»: {stage.get_status_display().lower()}.',
        )
    else:
        messages.info(request, 'Этап уже в этом статусе — отметили, что этапы сверены.')
    return redirect(_stages_url(project))


@login_required
def stages_confirm(request, pk):
    """«Этап актуален» — ничего не менялось, но ПМ сверил этапы сегодня."""
    project = services.pm_project_or_404(request.user, pk)
    if request.method == 'POST':
        stage_reminders.mark_checked(project)
        messages.success(request, 'Спасибо! Этапы сверены — напоминание погасло до завтра.')
    # возвращаем туда, где нажали кнопку, но только внутри сайта
    next_url = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        next_url = _stages_url(project)
    return redirect(next_url)


def _group_or_404(project):
    group = getattr(project, 'group', None)
    if group is None:
        raise Http404('У проекта ещё нет группы в потоке.')
    return group


@login_required
def meeting_detail(request, pk, meeting_pk):
    """Табель собрания у ПМ — только просмотр: отмечает и оценивает
    теперь тимлид, ПМ видит результат."""
    project = services.pm_project_or_404(request.user, pk)
    group = _group_or_404(project)
    meeting = get_object_or_404(GroupMeeting, pk=meeting_pk, group=group)
    marks = {mark.intern_id: mark for mark in meeting.attendance.all()}
    scores = {score.intern_id: score for score in meeting.scores.all()}
    previous = attendance_services.previous_scores(meeting)
    members = list(
        attendance_services.attendance_eligible_members(group)
        .select_related('intern__specialization')
        .filter(intern__isnull=False).order_by('role', 'intern__full_name'),
    )
    for member in members:
        member.mark = marks.get(member.intern_id)
        score = scores.get(member.intern_id)
        value = score.score if score else None
        member.score = attendance_services.score_row(
            meeting, member.intern, score=value,
            comment=score.comment if score else '',
            previous=previous.get(member.intern_id),
        )
    from apps.projects.portal_people import meeting_sheet_context

    return render(request, 'projects/portal/meeting_detail.html', meeting_sheet_context(
        project, meeting, members, 'pm', False,
        'scores' if request.GET.get('tab') == 'scores' else 'marks',
    ))


@login_required
def report_create(request, pk):
    project = services.pm_project_or_404(request.user, pk)
    if request.method == 'POST':
        text = request.POST.get('text', '').strip()
        if not text:
            messages.error(request, 'Отчёт пустой — напишите текст.')
        else:
            report = ProjectReport.objects.create(
                project=project, text=text, author=request.user,
            )
            messages.success(request, f'Отчёт от {report.date:%d.%m.%Y} сохранён.')
    return redirect(f"{reverse('pm_portal:project_detail', args=[project.pk])}?tab=report")


@login_required
def report_update(request, pk, report_pk):
    project = services.pm_project_or_404(request.user, pk)
    report = get_object_or_404(ProjectReport, pk=report_pk, project=project)
    if request.method == 'POST':
        text = request.POST.get('text', '').strip()
        if text:
            report.text = text
            report.save(update_fields=['text', 'updated_at'])
            messages.success(request, 'Отчёт обновлён.')
        else:
            messages.error(request, 'Отчёт пустой — текст не сохранён.')
    return redirect(f"{reverse('pm_portal:project_detail', args=[project.pk])}?tab=report")


@login_required
def report_delete(request, pk, report_pk):
    project = services.pm_project_or_404(request.user, pk)
    report = get_object_or_404(ProjectReport, pk=report_pk, project=project)
    if request.method == 'POST':
        date = report.date
        report.delete()
        messages.success(request, f'Отчёт от {date:%d.%m.%Y} удалён.')
    return redirect(f"{reverse('pm_portal:project_detail', args=[project.pk])}?tab=report")


def _team_url(project):
    return f"{reverse('pm_portal:project_detail', args=[project.pk])}?tab=team"


@login_required
def member_add(request, pk):
    project = services.pm_project_or_404(request.user, pk)
    role = _role_from(request)
    form = TeamMemberForm(request.POST or None, role=role, project=project)
    created_person = None
    if request.method == 'POST':
        data, created_person = _with_new_person(request, role=role)
        form = TeamMemberForm(data, role=role, project=project)
    if request.method == 'POST' and form.is_valid():
        member = form.save(commit=False)
        if member.role == TeamRole.PROJECT_MANAGER:
            messages.error(request, 'ПМа проекта (и стажёра направления PM) назначает руководитель.')
            return redirect(_team_url(project))
        member.project = project
        member.group = getattr(project, 'group', None)
        from apps.teams.services import save_membership

        closed = save_membership(member)
        if closed:
            messages.error(request, closed)
            return redirect(_team_url(project))
        from apps.teams.services import activate_intern_membership

        activate_intern_membership(member, user=request.user)
        warning = form.overload_warning()
        if warning:
            messages.warning(request, warning)
        if created_person:
            messages.success(request, f'{created_person} заведён(а) в базе.')
        messages.success(request, f'{member.person_name} добавлен(а) в команду.')
        return redirect(_team_url(project))
    return render(
        request, 'projects/portal/member_form.html',
        {'form': form, 'project': project, 'cancel_url': _team_url(project),
         'people': people_options(form),
         'specializations': Specialization.objects.order_by('name'),
         'role': role,
         'title': _title_for(role)},
    )


@login_required
def member_edit(request, pk, member_pk):
    project = services.pm_project_or_404(request.user, pk)
    member = get_object_or_404(TeamMember, pk=member_pk, project=project)
    form = TeamMemberEditForm(request.POST or None, instance=member)
    if request.method == 'POST' and form.is_valid():
        member = form.save(commit=False)
        from apps.teams.services import save_membership

        closed = save_membership(member)
        if closed:
            messages.error(request, closed)
            return redirect(_team_url(project))
        from apps.teams.services import activate_intern_membership

        activate_intern_membership(member, user=request.user)
        warning = form.overload_warning()
        if warning:
            messages.warning(request, warning)
        messages.success(request, 'Участник обновлён.')
        return redirect(_team_url(project))
    return render(
        request, 'projects/portal/member_form.html',
        {
            'form': form, 'project': project, 'cancel_url': _team_url(project),
            'remove_url': reverse('pm_portal:member_delete', args=[project.pk, member.pk])
            if member.status == TeamMember.Status.ACTIVE else '',
            'left_reasons': TeamMember.LeftReason.choices,
            'people': people_options(form),
            'specializations': Specialization.objects.order_by('name'),
            'selected_id': member.intern_id,
            'selected_name': member.person_name,
            'title': f'Редактирование: {member.person_name}',
        },
    )


@login_required
def member_delete(request, pk, member_pk):
    project = services.pm_project_or_404(request.user, pk)
    member = get_object_or_404(TeamMember, pk=member_pk, project=project)
    if request.method == 'POST':
        name = member.person_name
        team_services.leave_team(member, reason=request.POST.get('left_reason', ''))
        messages.success(request, f'{name} убран(а) из команды.')
    return redirect(_team_url(project))


@login_required
def intern_detail(request, pk, intern_pk):
    """Карточка стажёра — только по своему проекту и только активного
    участника, только чтение (UI-03C: 2.0)."""
    from apps.projects.portal_people import intern_card_context

    project = services.pm_project_or_404(request.user, pk)
    member = get_object_or_404(
        TeamMember.objects.select_related('intern__specialization'),
        project=project, intern_id=intern_pk, status=TeamMember.Status.ACTIVE,
    )
    return render(request, 'projects/portal/intern_detail.html', intern_card_context(project, member, 'pm'))


@login_required
def client_edit(request, pk):
    """Данные заказчика этого проекта — заполняет ПМ."""
    project = services.pm_project_or_404(request.user, pk)
    form = PMClientForm(request.POST or None, instance=project.client)
    if request.method == 'POST' and form.is_valid():
        client = form.save()
        if project.client_id != client.pk:
            project.client = client
            project.save(update_fields=['client', 'updated_at'])
        messages.success(request, 'Данные клиента сохранены.')
        return redirect(f"{reverse('pm_portal:project_detail', args=[project.pk])}?tab=client")
    return render(request, 'projects/portal/form_page.html', {
        'form': form, 'project': project, 'title': 'Данные заказчика', 'cols': 2,
        'cancel_url': f"{reverse('pm_portal:project_detail', args=[project.pk])}?tab=client",
    })


@login_required
def primary_stage_set(request, pk):
    """ПМ выбирает основной этап своего проекта. Чужой проект — 404."""
    from django.http import HttpResponseNotAllowed

    from apps.projects import services as project_services

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    project = services.pm_project_or_404(request.user, pk)
    stage = get_object_or_404(ProjectStage, pk=request.POST.get('stage') or 0, project=project)
    project_services.set_primary_stage(project, stage, request.user)
    messages.success(request, f'Основной этап: {stage.get_key_display()}.')
    return redirect(f"{reverse('pm_portal:project_detail', args=[project.pk])}?tab=stages")

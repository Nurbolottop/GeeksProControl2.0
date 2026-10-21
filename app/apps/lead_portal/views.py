import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.attendance import services as attendance_services
from apps.attendance.models import GroupMeeting, MeetingKind, WorkScore
from apps.lead_portal import services
from apps.lead_portal.forms import LeadToReserveForm
from apps.projects.services import calculate_deadline_status
from apps.teams import services as team_services
from apps.teams.forms import TeamMemberEditForm
from apps.teams.models import TeamMember, TeamRole
from apps.teams.views import people_options
from apps.training.models import Specialization


@login_required
def dashboard(request):
    """Список проектов, где текущий пользователь — активный тимлид."""
    from apps.notifications.services import personal
    from apps.projects.models import ProjectStatus
    from apps.reserve.services import reserve_card_of

    category = request.GET.get('status', 'all')
    projects = services.lead_projects(request.user)
    if category == 'in_progress':
        projects = projects.filter(status__in=[ProjectStatus.ACTIVE, ProjectStatus.PAUSED])
    elif category == 'completed':
        projects = projects.filter(status=ProjectStatus.COMPLETED)
    elif category == 'cancelled':
        projects = projects.filter(status__in=[ProjectStatus.CANCELLED, ProjectStatus.REFUSED])
    else:
        category = 'all'

    from apps.pm_portal.views import PORTAL_TABS
    from apps.projects.views import project_list_context

    base = projects
    projects = list(projects)
    for project in projects:
        project.pending_meeting = services.pending_meeting(project, request.user)
    list_context = project_list_context(request, base, 'lead_portal:project_detail', 'Мои проекты')
    pending = {p.pk: p.pending_meeting for p in projects}
    for project in list_context['page_obj'].object_list:
        project.pending_meeting = pending.get(project.pk)
    if request.htmx and request.htmx.target == 'project-list':
        return render(request, 'projects/_project_table.html', list_context)

    notifications = list(personal(getattr(request.user, 'intern_profile', None)))
    response = render(request, 'lead_portal/dashboard.html', {
        **list_context, 'category_tabs': PORTAL_TABS,
        'projects': projects,
        'resume': reserve_card_of(request.user),
        'notifications': notifications,
        'category': category,
    })
    # показали на главной — значит прочитано; висит, пока не нажмут «Понятно»
    unread = [note.pk for note in notifications if not note.is_read]
    if unread:
        from apps.notifications.models import Notification

        Notification.objects.filter(pk__in=unread).update(is_read=True)
    return response


@login_required
def notification_close(request, pk):
    """«Понятно» — закрыть своё уведомление. Чужие закрыть нельзя."""
    from apps.notifications.services import personal

    if request.method == 'POST':
        personal(getattr(request.user, 'intern_profile', None)).filter(pk=pk).update(
            is_closed=True, is_read=True,
        )
    return redirect('lead_portal:dashboard')


@login_required
def resume(request):
    """Моё резюме в резерве кадров: правлю сам, под своим логином, без ссылок.

    Тимлиды попадают в резерв автоматически — здесь человек ведёт своё
    резюме. Оценки, статусы и комментарии сотрудников сюда не
    попадают: форма та же, что у публичной анкеты.

    Открывается сначала в режиме просмотра — редактирование только по
    кнопке «Редактировать» (или после неудачной отправки формы).
    """
    import json

    from apps.reserve import services as reserve_services
    from apps.reserve.forms import ReserveApplyForm

    candidate = reserve_services.reserve_card_of(request.user)
    person = getattr(request.user, 'intern_profile', None)
    if candidate is None and person is not None:
        from apps.teams.models import TeamMember, TeamRole

        # тимлид всегда в резерве — если карточки почему-то нет, заводим сразу
        if TeamMember.objects.filter(intern=person, role=TeamRole.TEAM_LEAD).exists():
            reserve_services.ensure_lead_in_reserve(person)
            candidate = reserve_services.reserve_card_of(request.user)
    if candidate is None:
        return render(request, 'lead_portal/resume.html', {'candidate': None})

    editing = request.method == 'POST' or request.GET.get('edit') == '1'
    if not editing:
        return render(request, 'lead_portal/resume.html', {
            'candidate': candidate, 'editing': False,
        })

    form = ReserveApplyForm(
        request.POST or None, request.FILES or None, instance=candidate,
    )
    if request.method == 'POST' and form.is_valid():
        reserve_services.save_own_resume(candidate, form, request.user)
        messages.success(request, 'Резюме сохранено — в резерве кадров уже новая версия.')
        return redirect('lead_portal:resume')
    return render(request, 'lead_portal/resume.html', {
        'candidate': candidate,
        'form': form,
        'editing': True,
        'direction_groups': json.dumps(reserve_services.direction_groups_map()),
        'mark_optional': True,
    })


@login_required
def project_detail(request, pk):
    """Проект глазами тимлида (2.0, UI-03C): обзор своего направления,
    команда (ответственные и своё направление), задачи, табель, документы.
    Тимлид, который вёл уже закрытый проект, видит его только для чтения."""
    from apps.projects import portal

    project = services.lead_project_or_404(request.user, pk)
    project.deadline_status = calculate_deadline_status(project)
    tab = portal.valid_tab('lead', request.GET.get('tab', 'overview'))
    context = portal.card_context(request, project, 'lead', tab)
    context['pending_meeting'] = services.pending_meeting(project, request.user)
    if context['pending_meeting']:
        for item in context['tabs']:
            if item['key'] == 'attendance':
                item['count'] = '!'  # табель последнего собрания не закрыт
    return render(request, 'projects/card.html', context)


def _team_url(project):
    return f"{reverse('lead_portal:project_detail', args=[project.pk])}?tab=team"


def _own_direction_member_or_404(request, project, member_pk):
    """Тимлид правит/убирает только участников своего направления."""
    own_role = services.lead_own_role(request.user)
    return get_object_or_404(
        TeamMember, pk=member_pk, project=project, role=own_role,
    )


@login_required
def member_edit(request, pk, member_pk):
    project = services.active_lead_project_or_404(request.user, pk)
    member = _own_direction_member_or_404(request, project, member_pk)
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
            'remove_url': reverse('lead_portal:member_delete', args=[project.pk, member.pk])
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
    project = services.active_lead_project_or_404(request.user, pk)
    member = _own_direction_member_or_404(request, project, member_pk)
    if request.method == 'POST':
        name = member.person_name
        team_services.leave_team(member, reason=request.POST.get('left_reason', ''))
        messages.success(request, f'{name} убран(а) из команды.')
    return redirect(_team_url(project))


def _group_or_404(project):
    group = getattr(project, 'group', None)
    if group is None:
        raise Http404('У проекта ещё нет группы в потоке.')
    return group


@login_required
def meeting_create(request, pk):
    """Собрание своего направления: тимлид дизайна собирает дизайнеров,
    у бэкенда в этот же день может быть своё собрание.

    Новое собрание нельзя создать, пока не закрыто предыдущее — отмечать
    посещаемость и ставить оценки своему направлению обязательно (в
    отличие от ПМ, у которого это только просмотр)."""
    project = services.active_lead_project_or_404(request.user, pk)
    group = _group_or_404(project)
    if request.method == 'POST':
        pending = services.pending_meeting(project, request.user)
        if pending is not None:
            messages.error(
                request,
                f'Сначала закройте табель за {pending.date:%d.%m.%Y} — отметьте '
                'посещаемость и оценки всем стажёрам своего направления, '
                'только потом можно создать новое собрание.',
            )
            return redirect(
                f"{reverse('lead_portal:project_detail', args=[project.pk])}?tab=attendance",
            )
        raw = request.POST.get('date', '').strip()
        try:
            date = datetime.date.fromisoformat(raw)
        except ValueError:
            messages.error(request, 'Укажите корректную дату.')
        else:
            own_role = services.lead_own_role(request.user)
            meeting = attendance_services.create_meeting(
                group, kind=MeetingKind.INTERNAL, date=date,
                direction=own_role or '',
                host=getattr(request.user, 'intern_profile', None),
            )
            if meeting:
                messages.success(request, f'Собрание {date:%d.%m.%Y} добавлено.')
            else:
                messages.info(request, 'Такое собрание уже есть.')
    return redirect(f"{reverse('lead_portal:project_detail', args=[project.pk])}?tab=attendance")


def _lead_meeting_or_404(request, group, meeting_pk):
    """Собрание своего направления (или общее) — чужие тимлиду не открыть."""
    meetings = GroupMeeting.objects.filter(pk=meeting_pk, group=group)
    own_role = services.lead_own_role(request.user)
    if own_role:
        meetings = meetings.filter(direction__in=['', own_role])
    meeting = meetings.first()
    if meeting is None:
        raise Http404('Это собрание другого направления.')
    return meeting


def _own_direction_eligible_members(request, group):
    """Кого тимлид может отмечать/оценивать на собрании — только своё
    направление (ПМ и другие тимлиды и так исключены отдельно)."""
    members = attendance_services.attendance_eligible_members(group)
    own_role = services.lead_own_role(request.user)
    if own_role:
        members = members.filter(role=own_role)
    return members


@login_required
def meeting_detail(request, pk, meeting_pk):
    project = services.lead_project_or_404(request.user, pk)
    group = _group_or_404(project)
    meeting = _lead_meeting_or_404(request, group, meeting_pk)
    marks = {mark.intern_id: mark for mark in meeting.attendance.all()}
    scores = {score.intern_id: score for score in meeting.scores.all()}
    previous = attendance_services.previous_scores(meeting)
    members = list(
        _own_direction_eligible_members(request, group)
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

    is_active_lead = TeamMember.objects.filter(project=project, intern__user=request.user, role=TeamRole.TEAM_LEAD,
                                               status=TeamMember.Status.ACTIVE).exists()
    return render(request, 'projects/portal/meeting_detail.html', meeting_sheet_context(
        project, meeting, members, 'lead', is_active_lead,
        'scores' if request.GET.get('tab') == 'scores' else 'marks',
    ))


@login_required
def meeting_mark_toggle(request, pk, meeting_pk):
    """AJAX: клик по бейджу переключает отметку — Был → Не был → ... → пусто."""
    project = services.active_lead_project_or_404(request.user, pk)
    group = _group_or_404(project)
    meeting = _lead_meeting_or_404(request, group, meeting_pk)
    if request.method != 'POST':
        raise Http404
    member = get_object_or_404(
        _own_direction_eligible_members(request, group)
        .filter(intern__isnull=False), intern_id=request.POST.get('intern'),
    )
    member.mark = attendance_services.toggle_mark(meeting, member.intern, user=request.user)
    from apps.projects.portal_people import sheet_urls

    return render(request, 'projects/portal/_mark_row.html', {
        'project': project, 'meeting': meeting, 'member': member, 'editable': True,
        **sheet_urls(project, meeting, 'lead'),
    })


@login_required
def meeting_mark_all(request, pk, meeting_pk):
    project = services.active_lead_project_or_404(request.user, pk)
    group = _group_or_404(project)
    meeting = _lead_meeting_or_404(request, group, meeting_pk)
    if request.method == 'POST':
        created = attendance_services.mark_all_present(
            meeting, user=request.user, only_role=services.lead_own_role(request.user),
        )
        messages.success(request, f'Отмечено присутствующих: {created}.')
    return redirect('lead_portal:meeting_detail', pk=project.pk, meeting_pk=meeting.pk)


@login_required
def meeting_score(request, pk, meeting_pk):
    """Клик по шкале «Активность» — балл (0–10) или комментарий за период."""
    project = services.active_lead_project_or_404(request.user, pk)
    group = _group_or_404(project)
    meeting = _lead_meeting_or_404(request, group, meeting_pk)
    if request.method != 'POST':
        raise Http404
    member = get_object_or_404(
        _own_direction_eligible_members(request, group)
        .select_related('intern__specialization').filter(intern__isnull=False),
        intern_id=request.POST.get('intern'),
    )
    intern = member.intern
    entry = WorkScore.objects.filter(meeting=meeting, intern=intern).first()

    if 'comment' in request.POST:
        comment = request.POST.get('comment', '').strip()[:255]
        if entry:
            entry.comment = comment
            entry.save(update_fields=['comment', 'marked_by', 'updated_at'])
        elif comment:
            entry = WorkScore.objects.create(
                meeting=meeting, intern=intern,
                score=0, comment=comment, marked_by=request.user,
            )
    else:
        raw = request.POST.get('score', '')
        if raw.isdigit() and 0 <= int(raw) <= WorkScore.MAX:
            value = int(raw)
            if entry:
                entry.score = value
                entry.marked_by = request.user
                entry.save(update_fields=['score', 'marked_by', 'updated_at'])
            else:
                entry = WorkScore.objects.create(
                    meeting=meeting, intern=intern, score=value,
                    marked_by=request.user,
                )
        elif entry:
            entry.delete()
            entry = None

    member.score = attendance_services.score_row(
        meeting, intern,
        score=entry.score if entry else None,
        comment=entry.comment if entry else '',
        previous=attendance_services.previous_scores(meeting).get(intern.pk),
    )
    from apps.projects.portal_people import sheet_urls

    return render(request, 'projects/portal/_score_row.html', {
        'project': project, 'meeting': meeting, 'member': member, 'editable': True,
        **sheet_urls(project, meeting, 'lead'),
    })


def _own_member_or_404(user, pk, intern_pk, *, active_only=True):
    """Стажёр своей команды и своего направления — одна проверка на все
    действия тимлида с карточкой.

    ``active_only=False`` — вместе с теми, кто уже вышел из команды:
    карточку вышедшего тимлид смотрит и может отметить его в резерв,
    а замораживать стажировку у вышедшего нечего.
    """
    project = services.lead_project_or_404(user, pk)
    members = TeamMember.objects.select_related('intern__specialization').filter(
        project=project, intern_id=intern_pk, role=services.lead_own_role(user),
    )
    if active_only:
        members = members.filter(status=TeamMember.Status.ACTIVE)
    member = members.order_by('-status', '-joined_at').first()
    if member is None:
        raise Http404('Стажёр не из вашей команды или не вашего направления.')
    return project, member


def _intern_url(project, intern):
    return reverse('lead_portal:intern_detail', args=[project.pk, intern.pk])


@login_required
def intern_to_reserve(request, pk, intern_pk):
    """Отметить стажёра в резерв кадров.

    Тимлид может оставить комментарий — он знает человека по работе лучше
    всех; навыки и остальное резюме кандидат заполняет сам.
    """
    from apps.reserve.models import ReserveCandidate
    from apps.reserve.services import candidate_from_intern

    project, member = _own_member_or_404(request.user, pk, intern_pk, active_only=False)
    services.active_lead_project_or_404(request.user, project.pk)  # бывший тимлид — только просмотр
    intern = member.intern
    if ReserveCandidate.objects.filter(intern=intern).exists():
        messages.info(request, f'{intern.full_name} уже в резерве кадров.')
        return redirect(_intern_url(project, intern))

    form = LeadToReserveForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        candidate_from_intern(
            intern, request.user, comment_lead=form.cleaned_data['comment'],
        )
        messages.success(
            request,
            f'{intern.full_name} в резерве кадров — резюме он(а) дополнит сам(а).',
        )
        return redirect(_intern_url(project, intern))
    return render(request, 'projects/portal/form_page.html', {
        'project': project, 'intern': intern, 'form': form,
        'title': f'{intern.full_name} — в резерв кадров',
        'subtitle': 'Комментарий о работе видят только сотрудники. Навыки и опыт кандидат заполнит сам.',
        'submit_label': 'Добавить в резерв', 'cancel_url': _intern_url(project, intern),
    })


@login_required
def intern_detail(request, pk, intern_pk):
    """Карточка стажёра своего направления (2.0, UI-03C).

    Открывается и для бывших участников — история участия, оценки и
    задачи остаются видны, только чтение. Отметить в резерв может только
    активный тимлид проекта; заморозками и возвращениями управляет
    руководитель (apps.interns.freeze).
    """
    from apps.projects.portal_people import intern_card_context
    from apps.reserve.models import ReserveCandidate

    project, member = _own_member_or_404(request.user, pk, intern_pk, active_only=False)
    context = intern_card_context(project, member, 'lead')
    is_active_lead = TeamMember.objects.filter(
        project=project, intern__user=request.user, role=TeamRole.TEAM_LEAD, status=TeamMember.Status.ACTIVE,
    ).exists()
    context['in_reserve'] = ReserveCandidate.objects.filter(intern=member.intern).exists()
    context['to_reserve_url'] = (reverse('lead_portal:intern_to_reserve', args=[project.pk, member.intern_id])
                                 if is_active_lead else '')
    from apps.interns.discipline import can_warn

    context['warn_url'] = (reverse('lead_portal:intern_warn', args=[project.pk, member.intern_id])
                           if member.status == TeamMember.Status.ACTIVE and can_warn(request.user, member.intern, project)
                           else '')
    return render(request, 'projects/portal/intern_detail.html', context)


def _team_url(project):
    return f"{reverse('lead_portal:project_detail', args=[project.pk])}?tab=team"


@login_required
def profile_link_create(request, pk):
    """Ссылка на анкету для новых стажёров этого проекта.

    Тимлид отправляет её тем, с кем договорился: кто заполнит анкету,
    сразу окажется в команде проекта. Новая ссылка гасит прежнюю
    ссылку этого же проекта, ссылки других проектов не трогает.
    """
    from apps.interns.services import issue_profile_form_link

    project = services.active_lead_project_or_404(request.user, pk)
    if request.method == 'POST' and not project.accepts_new_members:
        messages.error(request, 'Набор в проект закрыт — ссылку на анкету выпустить нельзя.')
        return redirect(_team_url(project))
    if request.method == 'POST':
        raw_ttl = request.POST.get('ttl_days', '')
        ttl_days = int(raw_ttl) if raw_ttl.isdigit() else None
        issue_profile_form_link(request.user, ttl_days, project=project)
        messages.success(
            request,
            'Ссылка на анкету готова — отправьте её новому стажёру. '
            'Прежняя ссылка этого проекта больше не открывается.',
        )
    return redirect(_team_url(project))


@login_required
def profile_link_disable(request, pk):
    from apps.interns.services import active_profile_form_link

    project = services.active_lead_project_or_404(request.user, pk)
    if request.method == 'POST':
        link = active_profile_form_link(project)
        if link is not None:
            link.deactivate()
            messages.success(request, 'Ссылка на анкету отключена.')
    return redirect(_team_url(project))


@login_required
def intern_warn(request, pk, intern_pk):
    """Тимлид выдаёт предупреждение стажёру своего направления (UI-04B).
    Отчислять тимлид не может: три предупреждения уходят руководителю."""
    from django.core.exceptions import PermissionDenied

    from apps.interns import discipline
    from apps.interns.freeze import FreezeError

    project = services.active_lead_project_or_404(request.user, pk)
    _project, member = _own_member_or_404(request.user, project.pk, intern_pk)
    if request.method == 'POST':
        try:
            discipline.issue_warning(member.intern, project, request.POST.get('reason', ''), request.user)
        except (FreezeError, PermissionDenied) as error:
            messages.error(request, ' '.join(getattr(error, 'messages', None) or [str(error)]))
        else:
            messages.success(request, f'Предупреждение выдано: {member.intern.full_name}.')
    return redirect(_intern_url(project, member.intern))

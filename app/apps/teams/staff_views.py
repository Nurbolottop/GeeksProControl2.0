"""Разделы «ПМ» и «Тимлиды» (этап 6A, UI 2.0). Только руководитель
(RoleAccessMiddleware); каждое действие — POST и сервис apps.teams.staff."""
from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.interns.models import Intern, Position, StaffRole, StaffStatus
from apps.interns.services import branch_of
from apps.projects.models import Project, ProjectStatus
from apps.teams import staff
from apps.teams.forms import ROLE_BY_SPECIALIZATION
from apps.teams.models import TeamMember, TeamRole
from apps.training.models import Specialization

STAFF_TONES = {StaffStatus.ACTIVE: 'success', StaffStatus.DISMISSED: 'neutral', StaffStatus.STUDIO: 'primary'}


def _flash(request, error):
    messages.error(request, ' '.join(getattr(error, 'messages', None) or [str(error)]))


def _back(request, fallback):
    from django.utils.http import url_has_allowed_host_and_scheme

    target = request.POST.get('next', '')
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, require_https=request.is_secure()):
        return redirect(target)
    return redirect(fallback)


def _search(qs, params):
    q = (params.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(full_name__icontains=q) | Q(phone__icontains=q) | Q(telegram__icontains=q.lstrip('@')))
    if (params.get('direction') or '').isdigit():
        qs = qs.filter(specialization_id=params['direction'])
    if params.get('staff') in StaffStatus.values:
        qs = qs.filter(staff_status=params['staff'])
    return qs


def _decorate(people, role):
    """Назначения, участники направления, резерв — без N+1."""
    from apps.reserve.models import ReserveCandidate

    ids = [p.pk for p in people]
    members = defaultdict(list)
    for m in (TeamMember.objects.filter(intern_id__in=ids, role=role).select_related('project')
              .order_by('-status', '-joined_at')):
        members[m.intern_id].append(m)
    active_projects = {m.project_id for items in members.values() for m in items
                       if m.status == TeamMember.Status.ACTIVE and m.project_id}
    counts = defaultdict(int)
    for project_id, member_role in (TeamMember.objects.filter(project_id__in=active_projects,
                                                               status=TeamMember.Status.ACTIVE, intern__isnull=False)
                                    .values_list('project_id', 'role')):
        counts[(project_id, member_role)] += 1
    reserve = {c.intern_id: c for c in ReserveCandidate.objects.filter(intern_id__in=ids)}
    for person in people:
        items = members.get(person.pk, [])
        # текущие — активное назначение в идущем проекте; остальное — прошлое
        person.ui_active = [m for m in items if m.status == TeamMember.Status.ACTIVE
                            and m.project_id and m.project.status == ProjectStatus.ACTIVE]
        person.ui_past = [m for m in items if m not in person.ui_active]
        own = ROLE_BY_SPECIALIZATION.get(person.specialization.name if person.specialization_id else '')
        person.ui_people = sum(counts.get((m.project_id, own), 0) for m in person.ui_active) if own else 0
        person.ui_branch = branch_of(person)
        person.ui_reserve = reserve.get(person.pk)
        person.ui_staff_tone = STAFF_TONES.get(person.staff_status, 'neutral')
    return people


def _list(request, role, template, title):
    from apps.teams.selectors import lead_intern_ids

    if role == TeamRole.TEAM_LEAD:
        base = Intern.objects.filter(pk__in=lead_intern_ids() | set(
            Intern.objects.filter(staff_role=StaffRole.LEAD).values_list('pk', flat=True)))
    else:
        base = Intern.objects.filter(Q(team_memberships__role=TeamRole.PROJECT_MANAGER) | Q(staff_role=StaffRole.PM)).distinct()
    archived = []
    if role == TeamRole.TEAM_LEAD:
        archived = list(base.filter(is_archived=True).order_by('-archived_at'))
    if not request.GET.get('staff'):
        base = base.filter(is_archived=False)  # архив — отдельным блоком
    qs = _search(base.select_related('specialization', 'user'), request.GET).order_by('full_name', 'pk')
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    page.object_list = _decorate(list(page.object_list), role)
    return render(request, template, {
        'page': page, 'params': request.GET, 'title': title, 'total': base.count(), 'archived_leads': archived,
        'directions': Specialization.objects.order_by('name'), 'staff_statuses': StaffStatus.choices,
        'uncovered': staff.uncovered() if role == TeamRole.TEAM_LEAD else [],
        'projects': Project.objects.active().filter(status__in=[ProjectStatus.ACTIVE, ProjectStatus.PAUSED]).order_by('name'),
        'candidates': _assign_candidates(role),
        'crumbs': [{'label': 'Главная', 'href': reverse('dashboard')}, {'label': title, 'href': ''}],
    })


def _assign_candidates(role):
    qs = Intern.objects.filter(is_archived=False).select_related('specialization').order_by('full_name')
    if role == TeamRole.TEAM_LEAD:
        leads = [p for p in qs.filter(position=Position.TEAM_LEAD) if staff.is_staff_lead(p)]
        interns = list(qs.exclude(position=Position.TEAM_LEAD).exclude(staff_status__in=[StaffStatus.DISMISSED, StaffStatus.STUDIO])
                       .filter(status__in=['active', 'ready', 'waiting', 'employable'])
                       .exclude(specialization__name='PM')
                       .exclude(team_memberships__role=TeamRole.PROJECT_MANAGER, team_memberships__status='active'))
        return leads + interns  # стажёров — только с отметкой «повысить»
    return list(qs.exclude(staff_status__in=[StaffStatus.DISMISSED, StaffStatus.STUDIO])
                .filter(Q(specialization__name='PM') | Q(staff_role=StaffRole.PM)))


@login_required
def lead_list(request):
    return _list(request, TeamRole.TEAM_LEAD, 'teams/lead_list.html', 'Тимлиды')


@login_required
def pm_list(request):
    return _list(request, TeamRole.PROJECT_MANAGER, 'teams/pm_list.html', 'ПМ')


@login_required
def staff_detail(request, pk):
    """Карточка ПМ / тимлида: назначения, история, действия руководителя."""
    from apps.reserve.models import ReserveCandidate

    person = get_object_or_404(Intern.objects.select_related('specialization', 'user'), pk=pk)
    members = list(person.team_memberships.filter(role__in=[TeamRole.TEAM_LEAD, TeamRole.PROJECT_MANAGER])
                   .select_related('project').order_by('-status', '-joined_at', '-pk'))
    active = [m for m in members if m.status == TeamMember.Status.ACTIVE]
    for m in active:
        m.ui_modal, m.ui_form = f'replace-{m.pk}', f'replace-form-{m.pk}'
    is_lead = person.position == Position.TEAM_LEAD or any(m.role == TeamRole.TEAM_LEAD for m in members)
    reserve = ReserveCandidate.objects.filter(intern=person).first()
    from apps.audit.models import AuditLog

    return render(request, 'teams/staff_detail.html', {
        'person': person, 'active': active, 'past': [m for m in members if m not in active],
        'is_lead': is_lead, 'is_staff_lead': staff.is_staff_lead(person),
        'staff_tone': STAFF_TONES.get(person.staff_status, 'neutral'), 'branch': branch_of(person),
        'reserve': reserve,
        'reserve_label': f'Резерв: {reserve.get_status_display().lower()}' if reserve else '',
        'reserve_addable': not reserve or reserve.status not in ('reserve', 'employed'),
        'projects': Project.objects.active().filter(status__in=[ProjectStatus.ACTIVE, ProjectStatus.PAUSED]).order_by('name'),
        'lead_candidates': _assign_candidates(TeamRole.TEAM_LEAD),
        'pm_candidates': _assign_candidates(TeamRole.PROJECT_MANAGER),
        'can_dismiss': person.staff_status == StaffStatus.ACTIVE or (is_lead and not person.staff_status),
        'history': AuditLog.objects.filter(object_type='Intern', object_id=str(person.pk)).select_related('user')
        .order_by('-created_at')[:30],
        'crumbs': [{'label': 'Главная', 'href': reverse('dashboard')},
                   {'label': 'Тимлиды' if is_lead else 'ПМ',
                    'href': reverse('teams:lead_list' if is_lead else 'teams:pm_list')},
                   {'label': person.full_name, 'href': ''}],
    })


@login_required
@require_POST
def assign(request, role):
    project = Project.objects.filter(pk=request.POST.get('project') or 0).first()
    intern = Intern.objects.filter(pk=request.POST.get('intern') or 0).first()
    fallback = 'teams:lead_list' if role == 'lead' else 'teams:pm_list'
    if project is None or intern is None:
        messages.error(request, 'Выберите проект и человека.')
        return _back(request, fallback)
    try:
        if role == 'lead' and request.POST.get('promote') == '1' and not staff.is_staff_lead(intern):
            # явное решение руководителя: стажировка завершается, человек — выпускник и тимлид
            staff.promote_to_lead(intern, request.user)
            intern.refresh_from_db()
        if role == 'lead':
            _member, made = staff.assign_lead(project, intern, request.user)
        else:
            _member, made = staff.assign_pm(project, intern, request.user)
    except (staff.StaffError, PermissionDenied) as error:
        _flash(request, error)
    else:
        what = 'тимлид' if role == 'lead' else 'ПМ'
        messages.success(request, f'{intern.full_name} — {what} проекта «{project.name}».' if made
                         else f'{intern.full_name} уже {what} в «{project.name}».')
    return _back(request, fallback)


@login_required
@require_POST
def remove(request, pk):
    member = get_object_or_404(TeamMember.objects.select_related('intern', 'project'), pk=pk,
                               role__in=[TeamRole.TEAM_LEAD, TeamRole.PROJECT_MANAGER])
    try:
        staff.remove_assignment(member, request.user)
    except (staff.StaffError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, f'{member.person_name} снят(а) с проекта «{member.project.name}».')
    return _back(request, 'teams:lead_list' if member.role == TeamRole.TEAM_LEAD else 'teams:pm_list')


@login_required
@require_POST
def replace(request, pk):
    member = get_object_or_404(TeamMember.objects.select_related('intern', 'project'), pk=pk,
                               role__in=[TeamRole.TEAM_LEAD, TeamRole.PROJECT_MANAGER])
    new = Intern.objects.filter(pk=request.POST.get('intern') or 0).first()
    if new is None:
        messages.error(request, 'Выберите, кем заменить.')
        return _back(request, 'teams:lead_list')
    try:
        staff.replace(member, new, request.user)
    except (staff.StaffError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, f'«{member.project.name}»: {member.person_name} → {new.full_name}.')
    return _back(request, 'teams:lead_list' if member.role == TeamRole.TEAM_LEAD else 'teams:pm_list')


@login_required
@require_POST
def dismiss(request, pk):
    person = get_object_or_404(Intern, pk=pk)
    try:
        ended = staff.dismiss(person, request.user, request.POST.get('reason', ''))
    except (staff.StaffError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, f'{person.full_name}: работа в GeeksPro прекращена, закрыто назначений: {len(ended)}.')
    return redirect('teams:staff_detail', pk=person.pk)

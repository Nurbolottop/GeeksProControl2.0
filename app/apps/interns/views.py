from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import models
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.interns import services
from apps.interns.forms import (
    GrantAccessForm, InternEvaluationForm, InternForm, ProfileApplyForm,
    ResumeBankApplyForm, TalentReserveApplyForm, TalentReserveForm,
)
from apps.interns.models import (
    GraduateStatus, Intern, InternEvaluation, InternStatus,
    Position,
    ProfileFormLink, ProfileFormSubmission, ResumeBankStatus, TalentReserveCandidate,
)
from apps.teams.forms import ROLE_BY_SPECIALIZATION, InternProjectAddForm
from apps.teams.models import TeamMember, TeamRole


def lead_ids() -> set:
    """Тимлиды — сотрудники на зарплате, в списке стажёров их нет.

    ПМ в списке есть: они такие же стажёры, как остальные.
    """
    from apps.teams.selectors import staff_intern_ids

    return staff_intern_ids()


@login_required
def intern_list(request):
    """Стажёры (2.0, UI-04A): уникальные люди, фильтры, сортировка, страницы."""
    from apps.interns import people

    staff = people.staff_ids()
    graduates = people.graduate_ids()
    base = people.list_queryset(staff)
    qs = people.apply_filters(base, request.GET, graduates)
    qs, sort = people.apply_sort(qs, request.GET.get('sort'))
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    page.object_list = people.decorate(page.object_list, graduates)
    filters = ('q', 'branch', 'specialization', 'status', 'kind', 'project', 'telegram')
    context = {
        'page': page, 'params': request.GET, 'sort': sort,
        'has_filters': any(request.GET.get(key) for key in filters),
        'options': people.filter_options(staff),
        'total_people': base.count(),
        'waiting_count': (waiting := base.filter(status=InternStatus.WAITING, on_project=False).count()),
        'waiting_text': f'{waiting} — ждут, когда их добавят на проект.',
        'profile_link': services.active_profile_form_link(),
        'profile_link_ttls': PROFILE_LINK_TTL_CHOICES,
        'answers_count': ProfileFormSubmission.objects.count(),
        'crumbs': [{'label': 'Главная', 'href': reverse('dashboard')}, {'label': 'Стажёры', 'href': ''}],
    }
    return render(request, 'interns/list.html', context)


# Сроки жизни ссылки на анкету: значение для формы → подпись
PROFILE_LINK_TTL_CHOICES = [
    ('1', 'Сутки'),
    ('3', '3 дня'),
    ('7', '7 дней'),
    ('30', '30 дней'),
    ('', 'Без срока'),
]


@login_required
def profile_link_create(request):
    """Выпустить новую ссылку на публичную анкету (старая перестаёт работать)."""
    if request.method == 'POST':
        raw_ttl = request.POST.get('ttl_days', '')
        ttl_days = int(raw_ttl) if raw_ttl.isdigit() else None
        link = services.issue_profile_form_link(request.user, ttl_days)
        term = f'на {ttl_days} дн.' if ttl_days else 'без срока'
        messages.success(
            request,
            f'Новая ссылка на анкету создана ({term}), прежняя больше не '
            f'открывается: {request.build_absolute_uri(link.get_absolute_url())}',
        )
    return redirect('interns:list')


@login_required
def profile_link_answers(request):
    """Журнал заполнений анкеты — кто и по какой ссылке её прошёл."""
    submissions = (
        ProfileFormSubmission.objects
        .select_related('intern', 'link__project', 'link__created_by__intern_profile')
        .order_by('-created_at')
    )
    token = request.GET.get('link', '')
    if token:
        submissions = submissions.filter(link__token=token)
    paginator = Paginator(submissions, 50)
    return render(request, 'interns/profile_link_answers.html', {
        'page': paginator.get_page(request.GET.get('page')),
        'links': ProfileFormLink.objects.select_related('project')[:50],
        'token': token,
        'total': paginator.count,
    })


@login_required
def profile_link_disable(request):
    """Отключить действующую ссылку, не выпуская новую."""
    if request.method == 'POST':
        link = services.active_profile_form_link()
        if link is not None:
            link.deactivate()
            messages.success(request, 'Ссылка на анкету отключена.')
    return redirect('interns:list')


@login_required
def by_project(request):
    """Стажёры по проектам: где сколько людей, новых и старых.

    Направление — роль в команде проекта (как во вкладке «Команда»), а не
    специальность в профиле. ПМ и тимлиды — отдельными колонками, в счёт
    стажёров направлений не идут. Старый — подтверждённый выпускник
    (apps.dashboard.graduation), новый — все остальные. Один человек может
    быть на двух проектах, поэтому сумма по проектам больше, чем людей.
    """
    from apps.dashboard.graduation import compute
    from apps.projects.card import ROLE_LABELS
    from apps.projects.models import Project, ProjectStatus
    from apps.teams.models import TeamRole
    from apps.teams.rules import DIRECTION_ROLES

    leads = lead_ids()
    memberships = list(
        TeamMember.objects
        .filter(status=TeamMember.Status.ACTIVE, project__isnull=False)
        .select_related('intern', 'project', 'user')
    )
    graduates = compute([m.intern_id for m in memberships if m.intern_id]).ids

    def empty(project):
        return {'project': project, 'interns': [], 'specs': {}, 'pms': [], 'leads': [],
                'new': 0, 'old': 0}

    rows = {}
    for member in memberships:
        row = rows.setdefault(member.project_id, empty(member.project))
        if member.role == TeamRole.PROJECT_MANAGER:
            person = member.intern or member.user
            if person not in row['pms']:
                row['pms'].append(person)
            continue
        if member.intern_id is None:
            continue
        if member.intern_id in leads or member.role == TeamRole.TEAM_LEAD:
            if member.intern not in row['leads']:
                row['leads'].append(member.intern)
            continue
        if member.role not in DIRECTION_ROLES or member.intern in row['interns']:
            continue
        row['interns'].append(member.intern)
        is_old = member.intern_id in graduates
        row['old' if is_old else 'new'] += 1
        spec = row['specs'].setdefault(member.role, {'name': ROLE_LABELS.get(member.role, member.role),
                                                     'total': 0, 'new': 0, 'old': 0})
        spec['total'] += 1
        spec['old' if is_old else 'new'] += 1

    def finish(row):
        row['specs'] = sorted(row['specs'].values(), key=lambda item: (-item['total'], item['name']))
        return row

    projects = (
        Project.objects.active()
        .exclude(status__in=[
            ProjectStatus.COMPLETED, ProjectStatus.CANCELLED, ProjectStatus.REFUSED,
        ])
        .select_related('client')
    )
    table = [finish(rows.pop(project.pk, None) or empty(project)) for project in projects]
    # Команды на уже закрытых проектах, если кого-то забыли снять
    for row in rows.values():
        row['is_closed'] = True
        table.append(finish(row))
    table.sort(key=lambda row: (-len(row['interns']), row['project'].name))

    on_projects = {i.pk for row in table for i in row['interns']}
    busy_ids = {m.intern_id for m in memberships if m.intern_id}
    free = (
        Intern.objects.active()
        .exclude(pk__in=leads)
        .exclude(pk__in=busy_ids)
        .select_related('specialization')
        .order_by('full_name')
    )
    return render(request, 'interns/by_project.html', {
        'rows': table,
        'projects_count': sum(1 for row in table if row['interns']),
        'on_projects': len(on_projects),
        'on_projects_old': len(on_projects & graduates),
        'on_projects_new': len(on_projects - graduates),
        'pm_count': len({p.pk for row in table for p in row['pms'] if isinstance(p, Intern)}),
        'free': free,
    })


@login_required
def by_branch(request):
    """Стажёры по филиалам: сколько человек в Бишкеке и в Оше.

    Считаем ровно тех же людей, что видно в списке стажёров, чтобы
    цифра совпадала с числом строк при переходе по ней.
    """
    interns = list(
        Intern.objects.active()
        .exclude(pk__in=lead_ids())
        .select_related('specialization'),
    )
    summary = services.branch_summary(interns)
    return render(request, 'interns/by_branch.html', summary)


@login_required
def intern_detail(request, pk):
    """Карточка человека (2.0, UI-04A): один профиль — все стажировки,
    участия, выпуск, заморозки, оценки и события. Только руководитель."""
    from apps.interns import people

    intern = get_object_or_404(
        Intern.objects.select_related('specialization', 'training_group', 'team_lead', 'user'), pk=pk,
    )
    context = people.card(intern)
    memberships = context['active_memberships'] + context['past_memberships']
    roles = {m.role for m in context['active_memberships']}
    is_lead = intern.position == Position.TEAM_LEAD or TeamRole.TEAM_LEAD in roles
    from_graduates = request.GET.get('from') == 'graduates'
    context.update({
        'intern': intern,
        'is_lead': is_lead,
        'was_lead': any(m.role == TeamRole.TEAM_LEAD for m in memberships),
        'is_pm': TeamRole.PROJECT_MANAGER in roles,
        'criteria': InternEvaluation.CRITERIA,
        'kind_label': 'Тимлид' if is_lead else ('Выпускник' if context['is_graduate'] else 'Новый стажёр'),
        'warnings_label': f"Предупреждений: {context['active_warnings']}",
        'kind': 'Тимлид' if is_lead else 'Стажёр',
        'archived_title': f"В архиве с {intern.archived_at:%d.%m.%Y}" if intern.archived_at else 'В архиве',
        'archived_text': 'У нас больше не работает: снят(а) с текущих проектов. Оценки и история проектов сохранены.',
        'access_label': 'Сбросить пароль ПМ' if intern.user_id else 'Выдать доступ ПМ',
        'lead_access_label': 'Сбросить пароль тимлида' if intern.user_id else 'Выдать доступ тимлида',
        'pending_title': ', '.join(f'{r.get_kind_display()}: {r.reason[:60]}' for r in context['pending_requests']),
        'projects_subtitle': f"Всего участий: {len(memberships)} · история не теряется при вступлении в новый проект",
        'crumbs': [{'label': 'Главная', 'href': reverse('dashboard')},
                   {'label': 'Выпускники' if from_graduates else 'Стажёры',
                    'href': reverse('interns:graduates' if from_graduates else 'interns:list')},
                   {'label': intern.full_name, 'href': ''}],
    })
    return render(request, 'interns/detail.html', context)


@login_required
def intern_promote_lead(request, pk):
    """Повышение стажёра до штатного тимлида (этап 6A): стажировка
    завершается, человек — выпускник; проекты тимлида назначаются отдельно."""
    from apps.teams import staff

    intern = get_object_or_404(Intern, pk=pk)
    if request.method == 'POST':
        try:
            promoted = staff.promote_to_lead(intern, request.user)
        except (staff.StaffError, PermissionDenied) as error:
            _flash_error(request, error)
        else:
            if promoted:
                messages.success(request, f'{intern.full_name} — тимлид: стажировка завершена, статус выпускника '
                                          'сохранён. Назначьте проекты в разделе «Тимлиды».')
            else:
                messages.info(request, f'{intern.full_name} уже тимлид.')
    return redirect(intern.get_absolute_url())


@login_required
def intern_demote_lead(request, pk):
    """Снять с должности тимлида — человек возвращается в стажёры."""
    intern = get_object_or_404(Intern, pk=pk)
    if request.method == 'POST':
        if services.demote_from_lead(intern, user=request.user):
            messages.success(
                request, f'{intern.full_name} больше не тимлид.',
            )
        else:
            messages.info(request, f'{intern.full_name} и так не тимлид.')
    return redirect(intern.get_absolute_url())


@login_required
def intern_project_add(request, pk):
    """Добавить стажёра на проект прямо с его карточки."""
    intern = get_object_or_404(Intern, pk=pk)
    if intern.is_archived:
        messages.error(request, f'{intern.full_name} в архиве — сначала верните из архива.')
        return redirect(intern.get_absolute_url())
    form = InternProjectAddForm(
        request.POST or None, instance=TeamMember(intern=intern),
    )
    if request.method == 'POST' and form.is_valid():
        member = form.save(commit=False)
        member.group = getattr(member.project, 'group', None)
        if intern.position == Position.TEAM_LEAD:
            # Тимлид остаётся тимлидом и на новом проекте.
            member.role = TeamRole.TEAM_LEAD
        else:
            spec = intern.specialization
            member.role = ROLE_BY_SPECIALIZATION.get(
                spec.name if spec else '', TeamRole.OTHER,
            )
        member.joined_at = timezone.localdate()
        from apps.teams.services import save_membership

        closed = save_membership(member)
        if closed:
            messages.error(request, closed)
            return redirect(intern.get_absolute_url())
        update_fields = []
        if intern.status in (InternStatus.WAITING, InternStatus.READY):
            intern.status = InternStatus.ACTIVE
            update_fields.append('status')
        was_waiting = intern.graduate_status == GraduateStatus.WAITING
        if intern.graduate_status:
            from apps.audit.services import log as audit_log

            audit_log(
                intern, 'Статус выпускника снят',
                old_value=intern.get_graduate_status_display(),
                reason=f'переведён(а) на проект «{member.project.name}»',
                user=request.user,
            )
            intern.graduate_status = ''
            update_fields.append('graduate_status')
        if update_fields:
            intern.save(update_fields=[*update_fields, 'updated_at'])
        if was_waiting:
            # Человек ждал места и ему это обещали в боте — сообщаем сами.
            from apps.graduate_bot.services import notify_project_assigned

            notify_project_assigned(intern, member.project)
        messages.success(
            request, f'{intern.full_name} добавлен(а) в «{member.project.name}».',
        )
        return redirect(intern.get_absolute_url())
    return render(request, 'layouts/form_page.html', {
        'form': form, 'intern': intern, 'title': f'Добавить в проект: {intern.full_name}',
        'subtitle': 'Действуют правила набора и состава: новичок — один проект, выпускник — до двух.',
        'submit_label': 'Добавить', 'cancel_url': intern.get_absolute_url(),
    })


@login_required
def intern_project_remove(request, pk, member_pk):
    """Убрать стажёра с проекта прямо с его карточки."""
    from apps.teams import services as team_services

    intern = get_object_or_404(Intern, pk=pk)
    member = get_object_or_404(TeamMember, pk=member_pk, intern=intern)
    if request.method == 'POST':
        name = member.project.name if member.project_id else 'проекта'
        team_services.leave_team(member, reason=request.POST.get('left_reason', ''))
        messages.success(request, f'Убран(а) с «{name}».')
    return redirect(intern.get_absolute_url())


@login_required
def intern_create(request):
    form = InternForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        intern = form.save()
        messages.success(request, f'Стажёр {intern.full_name} добавлен(а).')
        return redirect(intern.get_absolute_url())
    return render(
        request, 'layouts/form_page.html', {'form': form, 'title': 'Новый стажёр', 'cols': 2,
                                             'cancel_url': reverse('interns:list')},
    )


@login_required
def intern_update(request, pk):
    intern = get_object_or_404(Intern, pk=pk)
    old_status = intern.status
    form = InternForm(request.POST or None, instance=intern)
    if request.method == 'POST' and form.is_valid():
        intern = form.save()
        if intern.status != old_status:
            # Дата смены статуса нужна недельной статистике (заморозки и
            # т.п.): без записи в журнале её больше нигде не узнать.
            from apps.audit.services import log as audit_log

            audit_log(
                intern, 'Статус изменён', user=request.user,
                old_value=InternStatus(old_status).label, new_value=intern.get_status_display(),
            )
        messages.success(request, 'Карточка стажёра обновлена.')
        return redirect(intern.get_absolute_url())
    return render(
        request, 'layouts/form_page.html',
        {'form': form, 'title': f'Редактирование: {intern.full_name}', 'cols': 2,
         'cancel_url': intern.get_absolute_url()},
    )


@login_required
def evaluation_add(request, pk):
    intern = get_object_or_404(Intern, pk=pk)
    form = InternEvaluationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        evaluation = form.save(commit=False)
        evaluation.intern = intern
        evaluation.evaluator = request.user
        services.add_evaluation(evaluation)
        messages.success(
            request,
            f'Оценка сохранена. Средний рейтинг: {intern.rating}.',
        )
        return redirect(intern.get_absolute_url())
    return render(
        request, 'layouts/form_page.html',
        {'form': form, 'intern': intern, 'title': f'Оценка: {intern.full_name}', 'cols': 2,
         'cancel_url': intern.get_absolute_url()},
    )


@login_required
def intern_archive(request, pk):
    """В архив: человек у нас больше не работает, но история остаётся."""
    intern = get_object_or_404(Intern, pk=pk)
    if request.method == 'POST' and not intern.is_archived:
        closed = services.archive_person(
            intern, user=request.user, reason=request.POST.get('reason', '').strip(),
        )
        note = f' и снят(а) с проектов: {closed}' if closed else ''
        messages.success(request, f'{intern.full_name} в архиве{note}.')
    return redirect(intern.get_absolute_url())


@login_required
def intern_unarchive(request, pk):
    intern = get_object_or_404(Intern, pk=pk)
    if request.method == 'POST' and intern.is_archived:
        services.unarchive_person(intern, user=request.user)
        messages.success(
            request,
            f'{intern.full_name} возвращён(а) из архива. На проекты назначьте заново.',
        )
    return redirect(intern.get_absolute_url())


@login_required
@require_POST
def intern_pause(request, pk):
    """Заморозить стажировку (UI-03C): руководитель оформляет и одобряет
    заявку с причиной — стажёр сразу выходит из всех активных проектов."""
    from apps.interns import freeze

    intern = get_object_or_404(Intern, pk=pk)
    try:
        req = freeze.freeze_by_head(intern, request.POST.get('reason', ''), request.user)
    except (freeze.FreezeError, PermissionDenied) as error:
        messages.error(request, ' '.join(getattr(error, 'messages', [str(error)])))
    else:
        left = req.left_projects.replace('\n', ', ') or 'активных проектов не было'
        messages.success(request, f'Стажировка {intern.full_name} заморожена. Вышел(а) из: {left}; '
                                  f'задач возвращено «К выполнению»: {req.tasks_reset}.')
    return redirect(intern.get_absolute_url())


@login_required
@require_POST
def intern_unpause(request, pk):
    """Возвращение из заморозки: прежнее участие не восстанавливается —
    стажёр выбирает новый проект (PM — назначает руководитель)."""
    from apps.interns import freeze

    intern = get_object_or_404(Intern, pk=pk)
    try:
        freeze.return_by_head(intern, request.POST.get('reason', ''), request.user)
    except (freeze.FreezeError, PermissionDenied) as error:
        messages.error(request, ' '.join(getattr(error, 'messages', [str(error)])))
    else:
        messages.success(request, f'{intern.full_name} возвращается: ожидает выбора нового проекта.')
    return redirect(intern.get_absolute_url())


@login_required
def internship_requests(request):
    """Решения по стажёрам (UI-03C/04B): три предупреждения, подтверждение
    продаж, заявки на заморозку, возвращение, отмену и повторную стажировку."""
    from apps.interns.models import DisciplineCase, InternshipRequest

    from apps.interns.models import SalesConfirmation

    base = InternshipRequest.objects.select_related('intern__specialization', 'decided_by', 'chosen_project', 'sales_by',
                                                    'project')
    pending = list(base.filter(status=InternshipRequest.Status.PENDING).order_by('created_at'))
    for req in pending:
        req.ui_modal, req.ui_form = f'req-approve-{req.pk}', f'req-approve-form-{req.pk}'
        req.ui_waits_sales = req.kind == InternshipRequest.Kind.REENROLL and req.sales_status != InternshipRequest.Sales.CONFIRMED
    cases = list(DisciplineCase.objects.filter(status=DisciplineCase.Status.PENDING)
                 .select_related('intern__specialization').prefetch_related('warnings__issued_by', 'warnings__project')
                 .order_by('created_at'))
    for case in cases:
        case.ui_keep, case.ui_expel = f'case-keep-{case.pk}', f'case-expel-{case.pk}'
    return render(request, 'interns/internship_requests.html', {
        'cases': cases,
        'sales_queue': [r for r in pending if r.kind == InternshipRequest.Kind.REENROLL
                        and r.sales_status == InternshipRequest.Sales.PENDING],
        'pending': [r for r in pending if not (r.kind == InternshipRequest.Kind.REENROLL
                                               and r.sales_status == InternshipRequest.Sales.PENDING)],
        'decided': base.exclude(status=InternshipRequest.Status.PENDING).order_by('-decided_at')[:50],
        'sales_events': SalesConfirmation.objects.exclude(status=SalesConfirmation.Status.APPLIED)[:20],
        'decided_cases': DisciplineCase.objects.exclude(status=DisciplineCase.Status.PENDING)
        .select_related('intern', 'decided_by').order_by('-decided_at')[:30],
        'crumbs': [{'label': 'Главная', 'href': reverse('dashboard')}, {'label': 'Решения по стажёрам', 'href': ''}],
    })


def _flash_error(request, error):
    messages.error(request, ' '.join(getattr(error, 'messages', None) or [str(error)]))


@login_required
@require_POST
def internship_request_decide(request, pk, decision):
    from apps.interns import freeze
    from apps.interns.models import InternshipRequest

    req = get_object_or_404(InternshipRequest, pk=pk)
    action = freeze.approve if decision == 'approve' else freeze.reject
    try:
        action(req, request.user, request.POST.get('comment', ''))
    except (freeze.FreezeError, PermissionDenied) as error:
        _flash_error(request, error)
    else:
        messages.success(request, f'Заявка «{req.get_kind_display().lower()}» ({req.intern.full_name}): '
                                  f'{"одобрена" if decision == "approve" else "отклонена"}.')
    return redirect('interns:internship_requests')


@login_required
@require_POST
def discipline_decide(request, pk, decision):
    from apps.interns import discipline, freeze
    from apps.interns.models import DisciplineCase

    case = get_object_or_404(DisciplineCase, pk=pk)
    try:
        discipline.decide_case(case, decision, request.user, request.POST.get('comment', ''))
    except (freeze.FreezeError, PermissionDenied) as error:
        _flash_error(request, error)
    else:
        messages.success(request, f'{case.intern.full_name}: ' + ('отчислен(а).' if decision == 'expel'
                                                                   else 'оставлен(а), счётчик предупреждений — 0.'))
    return redirect('interns:internship_requests')


@login_required
@require_POST
def intern_warn(request, pk):
    from apps.interns import discipline, freeze
    from apps.projects.models import Project

    intern = get_object_or_404(Intern, pk=pk)
    project = Project.objects.filter(pk=request.POST.get('project') or 0).first()
    try:
        discipline.issue_warning(intern, project, request.POST.get('reason', ''), request.user)
    except (freeze.FreezeError, PermissionDenied) as error:
        _flash_error(request, error)
    else:
        messages.success(request, f'Предупреждение выдано: {intern.full_name}.')
    return redirect(intern.get_absolute_url())


@login_required
@require_POST
def intern_cancel(request, pk):
    from apps.interns import discipline, freeze

    intern = get_object_or_404(Intern, pk=pk)
    try:
        discipline.cancel_by_head(intern, request.POST.get('reason', ''), request.user)
    except (freeze.FreezeError, PermissionDenied) as error:
        _flash_error(request, error)
    else:
        messages.success(request, f'{intern.full_name}: стажировка отменена (ушёл(ла) самостоятельно).')
    return redirect(intern.get_absolute_url())


@login_required
@require_POST
def intern_leave(request, pk):
    """Заявка выпускника на выход из одного из двух проектов (оформляет руководитель
    со слов стажёра; решение — в очереди «Решения по стажёрам»)."""
    from apps.interns import discipline, freeze
    from apps.projects.models import Project

    intern = get_object_or_404(Intern, pk=pk)
    project = Project.objects.filter(pk=request.POST.get('project') or 0).first()
    try:
        discipline.request_leave(intern, project, request.POST.get('reason', ''),
                                 source='head', user=request.user)
    except (freeze.FreezeError, PermissionDenied) as error:
        _flash_error(request, error)
    else:
        messages.success(request, 'Заявка на выход из проекта создана — решение на странице «Решения по стажёрам».')
    return redirect(intern.get_absolute_url())


@login_required
@require_POST
def intern_reenroll(request, pk):
    from apps.interns import discipline, freeze

    intern = get_object_or_404(Intern, pk=pk)
    try:
        discipline.request_reenroll(intern, request.POST.get('reason', ''), user=request.user)
    except (freeze.FreezeError, PermissionDenied) as error:
        _flash_error(request, error)
    else:
        messages.success(request, 'Заявка на повторную стажировку создана — нужна оплата (продажи) и решение руководителя.')
    return redirect(intern.get_absolute_url())


@login_required
def intern_delete(request, pk):
    """Удаление человека из базы вместе с участием в командах."""
    intern = get_object_or_404(Intern, pk=pk)
    if request.method == "POST":
        name = intern.full_name
        teams = intern.team_memberships.count()
        intern.delete()
        note = f" и снят(а) с проектов: {teams}" if teams else ""
        messages.success(request, f"{name} удалён(а) из базы{note}.")
        return redirect("interns:list")
    return redirect(intern.get_absolute_url())


@login_required
def _grant_role_access(request, pk, role, title):
    """Выдать (или сбросить) доступ в портал своей роли — логин по телефону.

    Общая логика для ПМ и тимлида: отличается только роль учётки и
    подпись формы, доступ у обоих устроен одинаково (Intern.user).
    """
    from apps.accounts.models import User

    intern = get_object_or_404(Intern, pk=pk)
    initial = {'username': intern.user.username if intern.user else intern.phone}
    form = GrantAccessForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        username = form.cleaned_data['username']
        password = form.cleaned_data['password']
        if intern.user:
            user = intern.user
            user.username = username
        else:
            user = User(username=username)
        user.role = role
        user.phone = intern.phone
        user.set_password(password)
        user.save()
        if not intern.user_id:
            intern.user = user
            intern.save(update_fields=['user', 'updated_at'])
        messages.success(request, f'Доступ выдан: логин «{username}».')
        return redirect(intern.get_absolute_url())
    return render(request, 'layouts/form_page.html', {
        'form': form, 'intern': intern, 'title': f'{title} — {intern.full_name}',
        'submit_label': 'Сбросить пароль' if intern.user else 'Выдать доступ',
        'cancel_url': intern.get_absolute_url(),
    })


def grant_pm_access(request, pk):
    """Выдать (или сбросить) доступ ПМа в его портал — логин по телефону."""
    from apps.accounts.models import User

    return _grant_role_access(
        request, pk, User.Role.PROJECT_MANAGER, 'Доступ в портал ПМ',
    )


def grant_lead_access(request, pk):
    """Выдать (или сбросить) доступ тимлида в его портал — логин по телефону."""
    from apps.accounts.models import User

    return _grant_role_access(
        request, pk, User.Role.TEAM_LEAD, 'Доступ в портал тимлида',
    )


@login_required
def reserve_list(request):
    """Резерв кадров — отдельный пул, не привязан к карточкам стажёров."""
    candidates = TalentReserveCandidate.objects.active().select_related('specialization')
    return render(request, 'interns/reserve_list.html', {
        'candidates': candidates,
    })


@login_required
def reserve_create(request):
    initial = {
        'full_name': request.GET.get('full_name', ''),
        'phone': request.GET.get('phone', ''),
        'email': request.GET.get('email', ''),
        'city': request.GET.get('city', ''),
    }
    form = TalentReserveForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        candidate = form.save()
        messages.success(request, f'«{candidate.full_name}» добавлен(а) в резерв.')
        return redirect('interns:reserve')
    return render(request, 'interns/reserve_form.html', {
        'form': form, 'title': 'Новый кандидат в резерве',
    })


@login_required
def reserve_update(request, pk):
    candidate = get_object_or_404(TalentReserveCandidate, pk=pk)
    form = TalentReserveForm(request.POST or None, instance=candidate)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Кандидат обновлён.')
        return redirect('interns:reserve')
    return render(request, 'interns/reserve_form.html', {
        'form': form, 'title': f'Резерв: {candidate.full_name}',
    })


@login_required
def reserve_delete(request, pk):
    candidate = get_object_or_404(TalentReserveCandidate, pk=pk)
    if request.method == 'POST':
        name = candidate.full_name
        candidate.delete()
        messages.success(request, f'{name} удалён(а) из резерва.')
    return redirect('interns:reserve')


@login_required
def reserve_set_priority(request, pk):
    candidate = get_object_or_404(TalentReserveCandidate, pk=pk)
    if request.method == 'POST':
        raw = request.POST.get('priority', '')
        if raw.isdigit():
            candidate.priority = int(raw)
            candidate.save(update_fields=['priority', 'updated_at'])
    return redirect('interns:reserve')


@login_required
def resume_bank_list(request):
    """Заявки в банк резюме — со статусом проверки и кнопками
    «Принять»/«На доработку»."""
    people = (
        Intern.objects.active().filter(in_resume_bank=True)
        .select_related('specialization')
        .order_by('resume_bank_status', 'full_name')
    )
    return render(request, 'interns/resume_bank_list.html', {
        'people': people, 'title': 'Банк резюме',
    })


@login_required
def resume_bank_approve(request, pk):
    intern = get_object_or_404(Intern, pk=pk)
    if request.method == 'POST':
        services.approve_resume_bank(intern, user=request.user)
        messages.success(request, f'{intern.full_name}: заявка принята.')
    return redirect('interns:resume_bank')


@login_required
def resume_bank_revise(request, pk):
    intern = get_object_or_404(Intern, pk=pk)
    if request.method == 'POST':
        comment = request.POST.get('comment', '').strip()
        if not comment:
            messages.error(request, 'Укажите комментарий — что нужно доработать.')
        else:
            services.revise_resume_bank(intern, comment, user=request.user)
            messages.success(request, f'{intern.full_name}: отправлено на доработку.')
    return redirect('interns:resume_bank')


@login_required
def graduates_list(request):
    """Выпускники (2.0, UI-04A): подтверждённый выпуск по
    apps.dashboard.graduation — тот же источник, что у Dashboard. Статус
    выпускника постоянный; дальнейшая деятельность — отдельной колонкой."""
    from apps.interns import people

    data = people.graduates(request.GET)
    page = Paginator(data['people'], 25).get_page(request.GET.get('page'))
    return render(request, 'interns/graduates.html', {
        'page': page, 'params': request.GET, 'total': data['total'], 'review_count': data['review_count'],
        'activity': data['activity'], 'activity_filters': people.ACTIVITY_FILTERS,
        'review_label': f"Записей на сверку: {data['review_count']} — их проверяет руководитель (manage.py graduate_reconciliation).",
        'options': people.filter_options(),
        'has_filters': any(request.GET.get(k) for k in ('q', 'branch', 'specialization', 'activity', 'status')),
        'crumbs': [{'label': 'Главная', 'href': reverse('dashboard')}, {'label': 'Выпускники', 'href': ''}],
    })


@login_required
def waiting_list(request):
    """«Ожидают проект» — кто сам записался через бота и ждёт распределения.

    Отсюда руководитель ставит человека на проект: после назначения бот
    сам пишет ему, что место нашлось (apps.interns.views.intern_project_add).
    """
    people = [
        person for person in services.graduated_interns()
        if person.graduate_status == GraduateStatus.WAITING
    ]
    return render(request, 'interns/waiting_list.html', {
        'people': people, 'title': 'Ожидают проект',
    })


def resume_bank_apply(request):
    """Публичная анкета «Банк резюме» — без входа в систему.

    По телефону ищем уже существующего стажёра, чтобы не плодить
    дубли, если человек уже есть в базе. Одна анкета на браузер —
    после отправки повторно её не откроешь (защита от спама).
    """
    if request.session.get('resume_bank_submitted'):
        return render(request, 'interns/resume_bank_apply_done.html')
    form = ResumeBankApplyForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        phone = form.cleaned_data['phone']
        intern = Intern.objects.filter(phone=phone).first()
        if intern is None:
            intern = form.save(commit=False)
        else:
            intern.full_name = form.cleaned_data['full_name']
            intern.email = form.cleaned_data['email']
            intern.specialization = form.cleaned_data['specialization']
        intern.in_resume_bank = True
        intern.resume_bank_status = ResumeBankStatus.PENDING
        intern.save()
        request.session['resume_bank_submitted'] = True
        return render(request, 'interns/resume_bank_apply_done.html')
    return render(request, 'interns/resume_bank_apply.html', {'form': form})


def profile_link_expired(request):
    """Старый постоянный адрес анкеты — только сообщение, что ссылка мертва."""
    return render(request, 'interns/profile_apply_expired.html', status=404)


def profile_apply(request, token):
    """Публичная анкета: стажёр сам заполняет/обновляет свой профиль.

    Открывается только по действующей ссылке (`token`): постоянного
    адреса у анкеты нет, новая ссылка гасит прежние. Если ссылка выпущена
    тимлидом для проекта — человек сразу попадает в его команду.

    Сначала ищем по телефону. У многих текущих записей телефон ещё не
    заполнен (карточку когда-то завели по одному ФИО) — тогда, чтобы не
    плодить дубль, ищем среди записей без телефона точное совпадение по
    имени. Если и это не помогло — считаем человека новым. Одна анкета
    на браузер и ссылку — после отправки повторно её не откроешь
    (защита от спама).
    """
    link = ProfileFormLink.objects.filter(token=token).first()
    if link is None or not link.is_open:
        return render(request, 'interns/profile_apply_expired.html', status=404)
    if link.project_id and not link.project.accepts_new_members:
        # Набор закрыт — ни новая, ни старая ссылка, ни повторная отправка
        # в команду не добавят; анкету в этом случае не принимаем вовсе.
        return render(request, 'interns/profile_apply_expired.html', {
            'message': 'Набор в этот проект сейчас закрыт. Когда набор откроют, тимлид пришлёт новую ссылку.',
        }, status=403)
    session_key = f'profile_submitted:{token}'
    if request.session.get(session_key):
        return render(request, 'interns/profile_apply_done.html')
    form = ProfileApplyForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        phone = form.cleaned_data['phone']
        full_name = form.cleaned_data['full_name']
        intern = Intern.objects.filter(phone=phone).first()
        if intern is None:
            intern = Intern.objects.filter(
                phone='', full_name__iexact=full_name,
            ).first()
        is_new = intern is None
        if is_new:
            intern = form.save(commit=False)
        else:
            for field in ProfileApplyForm.Meta.fields:
                setattr(intern, field, form.cleaned_data[field])
        intern.save()
        ProfileFormLink.objects.filter(pk=link.pk).update(
            submissions=models.F('submissions') + 1,
        )
        ProfileFormSubmission.objects.create(
            link=link, intern=intern, full_name=intern.full_name,
            phone=intern.phone, is_new=is_new,
        )
        if link.project_id:
            services.join_project_from_form(intern, link)
        request.session[session_key] = True
        return render(request, 'interns/profile_apply_done.html')
    return render(request, 'interns/profile_apply.html', {'form': form})


def talent_reserve_apply(request):
    """Публичная анкета «Резерв кадров» — без входа в систему.

    Тот же принцип поиска, что и в анкете профиля: сначала точное
    совпадение по телефону, иначе — по ФИО среди записей без телефона.
    Одна анкета на браузер — после отправки повторно её не откроешь
    (защита от спама).
    """
    if request.session.get('talent_reserve_submitted'):
        return render(request, 'interns/reserve_apply_done.html')
    form = TalentReserveApplyForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        phone = form.cleaned_data['phone']
        full_name = form.cleaned_data['full_name']
        candidate = TalentReserveCandidate.objects.filter(phone=phone).first()
        if candidate is None:
            candidate = TalentReserveCandidate.objects.filter(
                phone='', full_name__iexact=full_name,
            ).first()
        if candidate is None:
            candidate = form.save(commit=False)
        else:
            for field in TalentReserveApplyForm.Meta.fields:
                setattr(candidate, field, form.cleaned_data[field])
        candidate.save()
        request.session['talent_reserve_submitted'] = True
        return render(request, 'interns/reserve_apply_done.html')
    return render(request, 'interns/reserve_apply.html', {'form': form})

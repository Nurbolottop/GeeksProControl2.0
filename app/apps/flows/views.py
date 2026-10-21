from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.flows.models import Flow, Group
from apps.interns.models import Intern, InternStatus, WORKING_STATUSES
from apps.projects import selectors as project_selectors
from apps.projects.services import calculate_deadline_status
from apps.projects.models import Project, ProjectStatus
from apps.teams import selectors as team_selectors
from apps.teams.models import TeamMember


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ['number', 'name', 'project', 'comment']
        labels = {'number': 'Номер группы в потоке'}
        widgets = {
            'name': forms.TextInput(
                attrs={'placeholder': 'Например: команда Олимпийской школы'},
            ),
            'comment': forms.Textarea(attrs={'rows': 2}),
        }


class FlowForm(forms.ModelForm):
    class Meta:
        model = Flow
        fields = ['number', 'status', 'start_date', 'end_date', 'comment']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'end_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'comment': forms.Textarea(attrs={'rows': 2}),
        }


@login_required
def flow_list(request):
    flows = Flow.objects.annotate(
        projects_count=Count('projects', distinct=True),
        interns_count=Count('interns', distinct=True),
    ).order_by('-number')
    rows = []
    for flow in flows:
        active = flow.projects.filter(status=ProjectStatus.ACTIVE).count()
        completed = flow.projects.filter(status=ProjectStatus.COMPLETED).count()
        rows.append({
            'flow': flow,
            'projects': flow.projects_count,
            'interns': flow.interns_count,
            'active': active,
            'completed': completed,
        })
    return render(request, 'flows/list.html', {'rows': rows})


@login_required
def flow_detail(request, pk):
    flow = get_object_or_404(Flow, pk=pk)
    tab = request.GET.get('tab', 'groups')
    context = {'flow': flow, 'tab': tab}

    if tab == 'groups':
        groups = list(
            flow.groups.select_related('project', 'project__client')
            .prefetch_related('members__intern')
        )
        for group in groups:
            if group.project:
                group.project.deadline_status = calculate_deadline_status(
                    group.project,
                )
            group.active_count = sum(
                1 for m in group.members.all() if m.status == 'active'
            )
        context['groups'] = groups
        return render(request, 'flows/detail.html', context)

    if tab == 'roster':
        interns = (
            flow.interns.filter(is_archived=False)
            .select_related('specialization', 'training_group')
            .order_by('specialization__name', 'full_name')
        )
        busy = set(
            TeamMember.objects.filter(
                status=TeamMember.Status.ACTIVE, intern__isnull=False,
            ).values_list('intern_id', flat=True),
        )
        for intern in interns:
            intern.is_busy = intern.pk in busy
        # Ведомость разбита по направлениям
        by_spec: dict[str, list] = {}
        for intern in interns:
            key = str(intern.specialization) if intern.specialization else 'Без направления'
            by_spec.setdefault(key, []).append(intern)
        context['interns'] = interns
        context['roster_sections'] = [
            {'label': label, 'interns': people, 'count': len(people),
             'busy': sum(1 for i in people if i.is_busy)}
            for label, people in sorted(by_spec.items())
        ]
    elif tab == 'stats':
        context['by_spec'] = (
            flow.interns.filter(is_archived=False)
            .values('specialization__name')
            .annotate(total=Count('id'))
            .order_by('-total')
        )
        context['stats'] = {
            'projects': flow.projects.count(),
            'active': flow.projects.filter(status=ProjectStatus.ACTIVE).count(),
            'completed': flow.projects.filter(status=ProjectStatus.COMPLETED).count(),
            'stopped': flow.projects.filter(
                status__in=[ProjectStatus.CANCELLED, ProjectStatus.REFUSED],
            ).count(),
            'interns': flow.interns.filter(is_archived=False).count(),
            'working': flow.interns.filter(
                is_archived=False, status__in=WORKING_STATUSES,
            ).count(),
            'dropped': flow.interns.filter(
                is_archived=False, status=InternStatus.DROPPED,
            ).count(),
        }
    else:
        projects = list(
            flow.projects.select_related('client', 'project_type')
            .order_by('number_in_flow', 'pk')
        )
        project_selectors.annotate_deadline_statuses(projects)
        context['projects'] = projects
    return render(request, 'flows/detail.html', context)


@login_required
def group_detail(request, pk):
    """Карточка группы: состав команды и её проект."""
    group = get_object_or_404(
        Group.objects.select_related('flow', 'project', 'project__client'), pk=pk,
    )
    members = list(
        group.members.select_related('intern__specialization', 'user'),
    )
    if group.project:
        group.project.deadline_status = calculate_deadline_status(group.project)
    return render(request, 'flows/group_detail.html', {
        'group': group,
        'members': members,
        'sections': team_selectors.group_by_role(members),
        'active_members': [m for m in members if m.status == 'active'],
    })


@login_required
def group_create(request, flow_pk):
    flow = get_object_or_404(Flow, pk=flow_pk)
    next_number = (flow.groups.count() or 0) + 1
    form = GroupForm(request.POST or None, initial={'number': next_number})
    if request.method == 'POST' and form.is_valid():
        group = form.save(commit=False)
        group.flow = flow
        group.save()
        messages.success(request, f'{group} создана.')
        return redirect(group.get_absolute_url())
    return render(request, 'flows/group_form.html', {
        'form': form, 'flow': flow, 'title': f'Новая группа потока {flow.number}',
    })


@login_required
def group_update(request, pk):
    group = get_object_or_404(Group, pk=pk)
    form = GroupForm(request.POST or None, instance=group)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Группа обновлена.')
        return redirect(group.get_absolute_url())
    return render(request, 'flows/group_form.html', {
        'form': form, 'flow': group.flow, 'group': group,
        'title': f'Редактирование: {group}',
    })


@login_required
def flow_create(request):
    form = FlowForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        flow = form.save()
        messages.success(request, f'{flow} создан.')
        return redirect(flow.get_absolute_url())
    return render(
        request, 'flows/form.html', {'form': form, 'title': 'Новый поток'},
    )


@login_required
def flow_update(request, pk):
    flow = get_object_or_404(Flow, pk=pk)
    form = FlowForm(request.POST or None, instance=flow)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Поток обновлён.')
        return redirect(flow.get_absolute_url())
    return render(
        request, 'flows/form.html',
        {'form': form, 'flow': flow, 'title': f'Редактирование: {flow}'},
    )

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.training import importer, selectors
from apps.training.forms import ImportForm, TrainingGroupForm
from apps.training.models import BRANCHES, GroupStatus, Specialization, TrainingGroup


@login_required
def plan(request):
    """IT-академия: группы филиала по направлениям — как их присылает академия.

    Без ?specialization показываем только список направлений филиала:
    группы конкретного направления открываются, когда его выбрали.
    """
    tabs = selectors.branch_tabs()
    values = [tab['value'] for tab in tabs]
    branch = request.GET.get('branch')
    if branch not in values:
        # по умолчанию — первый филиал, где есть группы
        branch = next((tab['value'] for tab in tabs if tab['groups']), values[0])
    all_directions = selectors.academy_list(branch)
    directions = []
    specialization = None
    spec_id = request.GET.get('specialization')
    if spec_id and spec_id.isdigit():
        # группы показываем только у выбранного направления: без выбора
        # страница филиала — это список направлений, а не простыня групп
        directions = [
            d for d in all_directions if d['specialization'].pk == int(spec_id)
        ]
        if directions:
            specialization = directions[0]['specialization']
    return render(request, 'training/plan.html', {
        'tabs': tabs,
        'branch': branch,
        'current': next(tab for tab in tabs if tab['value'] == branch),
        'all_directions': all_directions,
        'directions': directions,
        'no_branch': selectors.NO_BRANCH,
        'specialization': specialization,
    })


@login_required
def group_list(request):
    """Группы академии: всё, из чего складывается план."""
    qs = TrainingGroup.objects.select_related('specialization')
    params = request.GET
    if params.get('specialization'):
        qs = qs.filter(specialization_id=params['specialization'])
    if params.get('branch') == selectors.NO_BRANCH:
        qs = qs.filter(branch='')
    elif params.get('branch'):
        qs = qs.filter(branch=params['branch'])
    groups = list(qs.order_by('end_date', 'specialization__name', 'number'))
    stage = params.get('status')
    if stage:
        groups = [group for group in groups if group.stage == stage]
    else:
        groups = [group for group in groups if group.is_open]
    return render(request, 'training/group_list.html', {
        'groups': groups,
        'params': params,
        'statuses': GroupStatus.choices,
        'specializations': Specialization.objects.all(),
        'branches': BRANCHES,
        'no_branch': selectors.NO_BRANCH,
    })


@login_required
def group_import(request):
    """Вставить сообщение академии: сначала показываем, что поменяется."""
    form = ImportForm(request.POST or None, initial={'branch': request.GET.get('branch')})
    rows = None
    if request.method == 'POST' and form.is_valid():
        rows = importer.parse(form.cleaned_data['text'], form.cleaned_data['branch'])
        if request.POST.get('confirm') and rows:
            result = importer.apply(rows)
            parts = [
                f'добавлено групп: {result["created"]}',
                f'обновлено: {result["updated"]}',
            ]
            if result['same']:
                parts.append(f'без изменений: {result["same"]}')
            if result['skipped']:
                parts.append(f'пропущено с ошибками: {result["skipped"]}')
            messages.success(request, 'Группы академии загружены — ' + ', '.join(parts) + '.')
            branches = {row.branch for row in rows if row.branch}
            target = branches.pop() if len(branches) == 1 else form.cleaned_data['branch']
            return redirect(f"{reverse('training:plan')}?branch={target}")
    return render(request, 'training/import.html', {
        'form': form,
        'rows': rows,
        'has_changes': bool(rows) and any(
            row.action in ('create', 'update') for row in rows
        ),
    })


@login_required
def group_create(request):
    form = TrainingGroupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        group = form.save()
        messages.success(request, f'Группа {group} добавлена.')
        return redirect('training:group_list')
    return render(request, 'training/group_form.html', {
        'form': form, 'title': 'Новая группа академии',
    })


@login_required
def group_update(request, pk):
    group = get_object_or_404(TrainingGroup, pk=pk)
    form = TrainingGroupForm(request.POST or None, instance=group)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Группа {group} обновлена.')
        return redirect('training:group_list')
    return render(request, 'training/group_form.html', {
        'form': form, 'title': f'Группа {group}', 'group': group,
    })


@login_required
def group_delete(request, pk):
    group = get_object_or_404(TrainingGroup, pk=pk)
    if request.method == 'POST':
        if group.interns.exists():
            messages.error(
                request,
                f'Группу {group} нельзя удалить: на неё ссылаются стажёры. '
                'Отметьте «Группа не состоялась».',
            )
        else:
            name = str(group)
            group.delete()
            messages.success(request, f'Группа {name} удалена.')
    return redirect('training:group_list')

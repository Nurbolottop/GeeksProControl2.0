from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.forms import inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render

from apps.projects.models import Project
from apps.resources import services
from apps.resources.models import PlannedProject, PlannedProjectNeed, StaffingRequest


class PlannedProjectForm(forms.ModelForm):
    class Meta:
        model = PlannedProject
        fields = [
            'name', 'status', 'probability', 'expected_start',
            'project_type', 'duration_months', 'comment',
        ]
        widgets = {
            'expected_start': forms.DateInput(
                attrs={'type': 'date'}, format='%Y-%m-%d',
            ),
            'comment': forms.Textarea(attrs={'rows': 2}),
        }


NeedFormSet = inlineformset_factory(
    PlannedProject, PlannedProjectNeed,
    fields=['specialization', 'count'], extra=2, can_delete=True,
)


@login_required
def forecast(request):
    """Прогноз потребности (ТЗ §14)."""
    return render(request, 'resources/forecast.html', {
        'rows': services.interns_summary(),
        'totals': services.interns_total(),
        'planned': (
            PlannedProject.objects
            .exclude(status__in=[
                PlannedProject.Status.LAUNCHED, PlannedProject.Status.REJECTED,
            ])
            .prefetch_related('needs__specialization')
        ),
    })


@login_required
def graduations(request):
    """Выпуски переехали в раздел «IT-академия» — там же план-график."""
    return redirect('training:plan')


@login_required
def planned_create(request):
    form = PlannedProjectForm(request.POST or None)
    formset = NeedFormSet(request.POST or None)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        planned = form.save()
        formset.instance = planned
        formset.save()
        messages.success(request, f'Планируемый проект «{planned.name}» создан.')
        return redirect('resources:forecast')
    return render(
        request, 'resources/planned_form.html',
        {'form': form, 'formset': formset, 'title': 'Планируемый проект'},
    )


@login_required
def planned_update(request, pk):
    planned = get_object_or_404(PlannedProject, pk=pk)
    form = PlannedProjectForm(request.POST or None, instance=planned)
    formset = NeedFormSet(request.POST or None, instance=planned)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        form.save()
        formset.save()
        messages.success(request, 'Планируемый проект обновлён.')
        return redirect('resources:forecast')
    return render(
        request, 'resources/planned_form.html',
        {'form': form, 'formset': formset, 'title': f'Редактирование: {planned.name}'},
    )


class StaffingRequestForm(forms.ModelForm):
    class Meta:
        model = StaffingRequest
        fields = ['project', 'specialization', 'count', 'needed_by', 'comment']
        widgets = {
            'needed_by': forms.DateInput(
                attrs={'type': 'date'}, format='%Y-%m-%d',
            ),
            'comment': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['project'].queryset = Project.objects.active().order_by('name')


@login_required
def staffing_requests(request):
    """Запросы на стажёров: сколько нужно, на какой проект, к какой дате."""
    form = StaffingRequestForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        req = form.save(commit=False)
        req.created_by = request.user
        req.save()
        messages.success(request, 'Запрос добавлен.')
        return redirect('resources:staffing_requests')
    qs = StaffingRequest.objects.select_related(
        'project', 'specialization', 'created_by',
    )
    open_requests = qs.filter(is_closed=False)
    by_spec = (
        open_requests.values('specialization__name')
        .annotate(total=Sum('count'))
        .order_by('-total', 'specialization__name')
    )
    return render(request, 'resources/staffing_requests.html', {
        'form': form,
        'open_requests': open_requests,
        'closed_requests': qs.filter(is_closed=True)[:20],
        'by_spec': by_spec,
        'total_needed': sum(row['total'] for row in by_spec),
    })


@login_required
def staffing_request_toggle(request, pk):
    """Закрыть запрос (человек нашёлся) или вернуть его в открытые."""
    req = get_object_or_404(StaffingRequest, pk=pk)
    if request.method == 'POST':
        req.is_closed = not req.is_closed
        req.save(update_fields=['is_closed', 'updated_at'])
    return redirect('resources:staffing_requests')


@login_required
def staffing_request_delete(request, pk):
    req = get_object_or_404(StaffingRequest, pk=pk)
    if request.method == 'POST':
        req.delete()
        messages.success(request, 'Запрос удалён.')
    return redirect('resources:staffing_requests')

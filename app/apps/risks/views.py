from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from apps.projects.models import Project
from apps.risks.models import DEFAULT_DELAY_REASONS, DelayReason, Risk, RiskCategory


def ensure_default_reasons() -> None:
    if not DelayReason.objects.exists():
        DelayReason.objects.bulk_create(
            [DelayReason(name=name) for name in DEFAULT_DELAY_REASONS],
        )


class RiskForm(forms.ModelForm):
    class Meta:
        model = Risk
        fields = ['project', 'category', 'description', 'delay_reason', 'status']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}


@login_required
def risk_list(request):
    ensure_default_reasons()
    qs = Risk.objects.select_related('project', 'delay_reason')
    params = request.GET
    if params.get('status'):
        qs = qs.filter(status=params['status'])
    else:
        qs = qs.filter(status=Risk.Status.OPEN)
    if params.get('category'):
        qs = qs.filter(category=params['category'])
    if params.get('project'):
        qs = qs.filter(project_id=params['project'])
    paginator = Paginator(qs, 50)
    page = paginator.get_page(params.get('page'))
    context = {
        'page': page,
        'params': params,
        'categories': RiskCategory.choices,
        'projects': Project.objects.active().order_by('name'),
    }
    return render(request, 'risks/list.html', context)


@login_required
def risk_create(request):
    ensure_default_reasons()
    initial = {}
    if request.GET.get('project'):
        initial['project'] = request.GET['project']
    form = RiskForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        risk = form.save(commit=False)
        risk.created_by = request.user
        risk.save()
        messages.success(request, 'Риск зафиксирован.')
        return redirect('risks:list')
    return render(
        request, 'risks/form.html', {'form': form, 'title': 'Новый риск'},
    )


@login_required
def risk_close(request, pk):
    risk = get_object_or_404(Risk, pk=pk)
    if request.method == 'POST':
        risk.status = Risk.Status.CLOSED
        risk.save(update_fields=['status', 'updated_at'])
        messages.success(request, 'Риск закрыт.')
    return redirect('risks:list')

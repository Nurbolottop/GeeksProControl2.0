from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.scripts.models import Script


class ScriptForm(forms.ModelForm):
    class Meta:
        model = Script
        fields = ['title', 'category', 'body', 'is_pinned']
        widgets = {
            'title': forms.TextInput(attrs={
                'placeholder': 'Например: Приглашение на собеседование',
            }),
            'category': forms.TextInput(attrs={
                'placeholder': 'Стажёрам, Клиентам…', 'list': 'script-categories',
            }),
            'body': forms.Textarea(attrs={'rows': 14}),
        }


def categories() -> list[str]:
    """Уже использованные категории — для подсказки и фильтра."""
    return sorted(
        value for value in
        Script.objects.exclude(category='')
        .values_list('category', flat=True).distinct()
    )


@login_required
def script_list(request):
    """Список скриптов: найти, развернуть, скопировать."""
    qs = Script.objects.all()
    params = request.GET
    search = params.get('q', '').strip()
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(body__icontains=search))
    if params.get('category'):
        qs = qs.filter(category=params['category'])
    return render(request, 'scripts/list.html', {
        'scripts': qs,
        'params': params,
        'categories': categories(),
        'total': Script.objects.count(),
    })


@login_required
def script_create(request):
    form = ScriptForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        script = form.save(commit=False)
        script.created_by = request.user
        script.save()
        messages.success(request, f'Скрипт «{script.title}» сохранён.')
        return redirect('scripts:list')
    return render(request, 'scripts/form.html', {
        'form': form, 'title': 'Новый скрипт', 'categories': categories(),
    })


@login_required
def script_update(request, pk):
    script = get_object_or_404(Script, pk=pk)
    form = ScriptForm(request.POST or None, instance=script)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Скрипт обновлён.')
        return redirect('scripts:list')
    return render(request, 'scripts/form.html', {
        'form': form, 'title': 'Редактирование скрипта',
        'script': script, 'categories': categories(),
    })


@login_required
def script_delete(request, pk):
    script = get_object_or_404(Script, pk=pk)
    if request.method == 'POST':
        title = script.title
        script.delete()
        messages.success(request, f'Скрипт «{title}» удалён.')
    return redirect('scripts:list')


@login_required
def script_pin(request, pk):
    """Закрепить сверху или снять закрепление."""
    script = get_object_or_404(Script, pk=pk)
    if request.method == 'POST':
        script.is_pinned = not script.is_pinned
        script.save(update_fields=['is_pinned', 'updated_at'])
    return redirect('scripts:list')

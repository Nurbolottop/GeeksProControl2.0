"""Главная страница руководителя (UI-02).

Доступ: только полный доступ (руководитель, администратор) — остальных
не пускает RoleAccessMiddleware, ПМ и тимлидов уводят их middleware.
Все цифры считает apps.dashboard.metrics / selectors, шаблоны только выводят.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from apps.dashboard import metrics, selectors
from apps.teams.selectors import staff_intern_ids


@login_required
def dashboard(request):
    today = timezone.localdate()
    week = metrics.last_complete_week(today)
    # Общие множества считаем один раз на страницу
    staff = staff_intern_ids()
    grad = metrics.graduation()
    context = {
        'generated_at': timezone.localtime(),
        'people': metrics.people_kpi(staff, grad.ids),
        'graduation_review': grad.review,
        'projects': metrics.project_kpi(),
        'by_stage': metrics.projects_by_stage(),
        'branches': metrics.branch_table(staff, grad.ids),
        'activity': metrics.weekly_activity(week, today, staff, grad),
        'activity_weeks': _activity_weeks(today),
        'attention': selectors.attention_items(),
        'pending': selectors.pending_decisions(),
        'compare_weeks': metrics.recent_weeks(8, today),
        'compare_default': week,
    }
    context['pending_total'] = sum(queue['count'] for queue in context['pending'])
    context['pending_label'] = f"Ожидают решения · {context['pending_total']}"
    return render(request, 'dashboard/index.html', context)


def _activity_weeks(today):
    current = metrics.week_of(today, today)
    return [metrics.last_complete_week(today), current]


@login_required
def week_activity(request):
    """Блок «Неделя» для выбранной недели (htmx-вкладки)."""
    today = timezone.localdate()
    week = metrics.parse_week(request.GET.get('week'), today)
    return render(request, 'dashboard/_week.html', {
        'activity': metrics.weekly_activity(week, today),
        'activity_weeks': _activity_weeks(today),
    })


@login_required
def compare(request):
    """Сравнение недели с предыдущей. По умолчанию — две последние полные недели."""
    today = timezone.localdate()
    week = metrics.parse_week(request.GET.get('week'), today)
    context = {
        'comparison': metrics.compare_weeks(week, today),
        'compare_weeks': metrics.recent_weeks(8, today),
    }
    template = 'dashboard/_compare.html' if request.htmx else 'dashboard/compare.html'
    return render(request, template, context)

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render

from apps.notifications import services
from apps.notifications.models import Notification


@login_required
def notification_list(request):
    qs = services.feed().filter(is_closed=False)
    level = request.GET.get('level')
    if level:
        qs = qs.filter(level=level)
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page'))
    # Открытие центра уведомлений отмечает текущую страницу прочитанной
    Notification.objects.filter(
        pk__in=[n.pk for n in page.object_list], is_read=False,
    ).update(is_read=True)
    return render(
        request, 'notifications/list.html',
        {'page': page, 'level': level or ''},
    )


@login_required
def notification_close(request, pk):
    if request.method == 'POST':
        services.feed().filter(pk=pk).update(is_closed=True, is_read=True)
    return redirect('notifications:list')


@login_required
def notification_close_all(request):
    if request.method == 'POST':
        services.feed().filter(is_closed=False).update(
            is_closed=True, is_read=True,
        )
    return redirect('notifications:list')

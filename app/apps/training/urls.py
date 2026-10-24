from functools import wraps

from django.conf import settings
from django.http import Http404
from django.urls import path

from apps.training import views

app_name = 'training'


def enabled(view):
    """Раздел скрыт, пока ACADEMY_ENABLED выключен (11F)."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not settings.ACADEMY_ENABLED:
            raise Http404
        return view(request, *args, **kwargs)
    return wrapper


urlpatterns = [
    path('', enabled(views.plan), name='plan'),
    path('groups/', enabled(views.group_list), name='group_list'),
    path('groups/create/', enabled(views.group_create), name='group_create'),
    path('groups/import/', enabled(views.group_import), name='group_import'),
    path('groups/<int:pk>/edit/', enabled(views.group_update), name='group_update'),
    path('groups/<int:pk>/delete/', enabled(views.group_delete), name='group_delete'),
]

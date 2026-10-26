from functools import wraps

from django.conf import settings
from django.http import Http404
from django.urls import path

from apps.scripts import views

app_name = 'scripts'


def enabled(view):
    """Раздел скрыт, пока SCRIPTS_ENABLED выключен (11F)."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not settings.SCRIPTS_ENABLED:
            raise Http404
        return view(request, *args, **kwargs)
    return wrapper


urlpatterns = [
    path('', enabled(views.script_list), name='list'),
    path('create/', enabled(views.script_create), name='create'),
    path('<int:pk>/edit/', enabled(views.script_update), name='update'),
    path('<int:pk>/delete/', enabled(views.script_delete), name='delete'),
    path('<int:pk>/pin/', enabled(views.script_pin), name='pin'),
]

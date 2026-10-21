from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import path

from apps.accounts.views import RoleAwareLoginView, dev_login

urlpatterns = [
    path('login/', RoleAwareLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
]

if settings.DEBUG:
    urlpatterns += [path('dev-login/', dev_login, name='dev_login')]

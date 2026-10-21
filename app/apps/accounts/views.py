from django.conf import settings
from django.contrib.auth import get_user_model, login, views as auth_views
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts.forms import PhoneAuthenticationForm
from apps.accounts.models import User


class RoleAwareLoginView(auth_views.LoginView):
    """ПМ после входа попадает в свой портал, остальные — как раньше."""

    template_name = 'accounts/login.html'
    authentication_form = PhoneAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        if self.request.user.role == User.Role.PROJECT_MANAGER:
            return reverse('pm_portal:dashboard')
        if self.request.user.role == User.Role.TEAM_LEAD:
            return reverse('lead_portal:dashboard')
        return super().get_success_url()


def dev_login(request):
    """DEBUG-only: авто-вход для локальных скриншотов. В проде недоступен."""
    if not settings.DEBUG:
        raise Http404
    user = get_user_model().objects.filter(is_superuser=True).first()
    if user:
        login(request, user)
    return redirect(request.GET.get('next', '/'))

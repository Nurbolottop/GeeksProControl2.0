from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render

from apps.accounts import access
from apps.accounts.models import User

ALLOWED_PREFIXES = ('/pm/', '/login/', '/logout/', '/static/', '/media/')
LEAD_ALLOWED_PREFIXES = ('/lead/', '/login/', '/logout/', '/static/', '/media/')


class PMScopeMiddleware:
    """Двусторонняя граница между обычным сайтом и порталом ПМ.

    ПМ видит только /pm/ — ни общий сайдбар, ни чужие проекты. И наоборот:
    /pm/ — только для ПМ, остальным (head/administrator) там делать
    нечего, их уводит на обычный дашборд.

    Один choke point вместо декоратора на каждый view: система устроена
    так, что новые/старые страницы автоматически закрыты для ПМ по
    умолчанию, если явно не в allowlist.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated:
            is_pm = user.role == User.Role.PROJECT_MANAGER
            if is_pm and not request.path.startswith(ALLOWED_PREFIXES):
                return redirect('pm_portal:dashboard')
            if not is_pm and request.path.startswith('/pm/'):
                # Тимлида уводим сразу в его портал, а не на общий
                # сайт — иначе LeadScopeMiddleware следующим запросом
                # тут же отправит его обратно и получится зацикливание.
                if user.role == User.Role.TEAM_LEAD:
                    return redirect('lead_portal:dashboard')
                return redirect('/')
        return self.get_response(request)


class LeadScopeMiddleware:
    """Та же граница, что и у ПМ (см. PMScopeMiddleware), но для
    тимлидов и портала /lead/ — независимо друг от друга, роль у
    пользователя ровно одна."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated:
            is_lead = user.role == User.Role.TEAM_LEAD
            if is_lead and not request.path.startswith(LEAD_ALLOWED_PREFIXES):
                return redirect('lead_portal:dashboard')
            if not is_lead and request.path.startswith('/lead/'):
                # Симметрично PMScopeMiddleware — ПМ уводим в его
                # портал напрямую, без зацикливания через общий сайт.
                if user.role == User.Role.PROJECT_MANAGER:
                    return redirect('pm_portal:dashboard')
                return redirect('/')
        return self.get_response(request)


class RoleAccessMiddleware:
    """Default deny для веб-интерфейса Control.

    Вошедший пользователь без полного доступа и не из портала (стажёр,
    неизвестная или пустая роль) видит только публичные страницы — формы
    по ссылкам, вход/выход, статику. Любой другой адрес, GET или POST,
    получает 403 до вызова view: проверка не зависит от того, есть ли
    декоратор у конкретной страницы и показан ли пункт в меню.

    ПМ и тимлидов здесь не трогаем — их границы держат PMScopeMiddleware и
    LeadScopeMiddleware ниже по цепочке.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if (
            user.is_authenticated
            and not access.has_full_access(user)
            and not access.is_portal_user(user)
            and not access.is_public_path(request.path)
        ):
            if request.headers.get('HX-Request'):
                return HttpResponseForbidden('Недостаточно прав.')
            return render(request, 'accounts/no_access.html', status=403)
        return self.get_response(request)

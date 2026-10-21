from django.utils.functional import SimpleLazyObject

from apps.accounts.models import User
from apps.ui import navigation


# Подпись роли в меню профиля — по-русски и коротко (в модели у ПМ и
# тимлида английские названия, их показывать в интерфейсе не нужно).
ROLE_LABELS = {
    User.Role.HEAD: 'Руководитель',
    User.Role.PROJECT_MANAGER: 'ПМ',
    User.Role.TEAM_LEAD: 'Тимлид',
    User.Role.INTERN: 'Стажёр',
    User.Role.ADMINISTRATOR: 'Администратор',
}


def can_view_catalog(user) -> bool:
    """Каталог компонентов /ui/ — инструмент разработки: только персонал с
    полным доступом (руководитель, администратор, superuser) и только вне
    production. Флаг is_staff сам по себе доступа не даёт."""
    from django.conf import settings

    from apps.accounts.access import has_full_access

    return settings.GP2_ENVIRONMENT != 'production' and has_full_access(user)


def _bell(request) -> dict | None:
    """Колокольчик в топбаре: куда ведёт и сколько непрочитанного."""
    from django.urls import reverse

    kind = navigation.nav_kind(request.user)
    if kind == 'head':
        return {'href': reverse('notifications:list'), 'count': navigation.unread_notifications(request)}
    if kind == 'lead':
        from apps.notifications.services import personal

        intern = getattr(request.user, 'intern_profile', None)
        return {'href': reverse('lead_portal:dashboard') + '#notifications', 'count': personal(intern).count()}
    return None


def ui(request):
    """Всё для каркаса — лениво: htmx-фрагменты меню не строят."""
    if not request.user.is_authenticated:
        return {}
    return {
        'ui_nav': SimpleLazyObject(lambda: navigation.build(request)),
        'ui_bell': SimpleLazyObject(lambda: _bell(request) or {}),
        'ui_can_view_catalog': can_view_catalog(request.user),
        'ui_role_label': ROLE_LABELS.get(request.user.role, ''),
    }

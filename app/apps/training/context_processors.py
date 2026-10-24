from django.conf import settings
from django.utils.functional import SimpleLazyObject

from apps.training import selectors


def academy_nav(request):
    """Дерево «филиал → направление» для меню IT-академии (ленивое)."""
    if not request.user.is_authenticated or not settings.ACADEMY_ENABLED:
        return {}
    return {'academy_enabled': True, 'academy_nav': SimpleLazyObject(selectors.nav_tree)}

from django.utils.functional import SimpleLazyObject

from apps.training import selectors


def academy_nav(request):
    """Дерево «филиал → направление» для меню IT-академии (ленивое)."""
    if not request.user.is_authenticated:
        return {}
    return {'academy_nav': SimpleLazyObject(selectors.nav_tree)}

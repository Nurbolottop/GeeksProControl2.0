"""Права доступа к резерву кадров.

Опираемся на существующие роли (`accounts.User.Role`):
смотреть резерв может любой вошедший сотрудник (ПМов в общий сайт всё
равно не пускает PMScopeMiddleware), а менять внутренние данные —
оценки, статусы, рекомендации — только руководитель и администратор.
Кандидат ничего этого не видит: у него лишь публичная анкета по токену.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from apps.accounts.models import User

EDITOR_ROLES = {User.Role.HEAD, User.Role.ADMINISTRATOR}


def can_edit_reserve(user) -> bool:
    return bool(
        user.is_authenticated
        and (user.is_superuser or user.role in EDITOR_ROLES),
    )


def reserve_editor_required(view):
    """Изменение резерва — только для ролей с правом редактирования."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not can_edit_reserve(request.user):
            raise PermissionDenied('Недостаточно прав для изменения резерва кадров.')
        return view(request, *args, **kwargs)

    return wrapper

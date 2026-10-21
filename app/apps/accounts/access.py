"""Кто и куда может входить в веб-интерфейс Control.

Модель доступа 2.0:
  * руководитель и администратор (и технический superuser) — вся платформа;
  * ПМ — только портал /pm/ и свои проекты (PMScopeMiddleware + pm_project_or_404);
  * тимлид — только портал /lead/, свои проекты и направление
    (LeadScopeMiddleware + lead_project_or_404);
  * стажёр и любая другая или неизвестная роль — веб-кабинета нет, работа
    через Telegram-бота. Им открыты только публичные страницы.

Default deny: всё, что не разрешено явно, запрещено — новый раздел
автоматически закрыт для ролей без полного доступа.
"""
from apps.accounts.models import User

FULL_ACCESS_ROLES = frozenset({User.Role.HEAD, User.Role.ADMINISTRATOR})
PORTAL_ROLES = frozenset({User.Role.PROJECT_MANAGER, User.Role.TEAM_LEAD})

# Маршруты, доступные без роли персонала (в том числе вошедшему стажёру):
# вход/выход, статика, файлы (их права проверяет apps.common.media),
# проверка здоровья и публичные формы по ссылкам из core/urls.py.
PUBLIC_PREFIXES = (
    '/login/', '/logout/', '/static/', '/media/', '/healthz/', '/readyz/',
    '/resume-bank/', '/intern-profile/', '/talent-reserve/',
    '/reserve-profile/', '/candidate/', '/brief/',
    # Видеособрания (этап 9): персональная ссылка участника по секретному
    # токену и вебхук видеосервиса с проверкой подписи — без входа в Control
    '/video/join/', '/video/enter/', '/video/webhook/',
)


def has_full_access(user) -> bool:
    """Руководитель, администратор или технический superuser."""
    return bool(
        user.is_authenticated
        and (user.is_superuser or user.role in FULL_ACCESS_ROLES)
    )


def is_portal_user(user) -> bool:
    """ПМ или тимлид: их границы держат PMScopeMiddleware/LeadScopeMiddleware
    (по роли — так же, как они сами её определяют)."""
    return bool(user.is_authenticated and user.role in PORTAL_ROLES)


def is_public_path(path: str) -> bool:
    return path.startswith(PUBLIC_PREFIXES)

"""Меню единого каркаса 2.0 — одно на все роли.

Что видит пользователь, решает его роль; что ему реально доступно, по-прежнему
решает сервер: PMScopeMiddleware и LeadScopeMiddleware пускают ПМ только в
/pm/, тимлида — только в /lead/, а объекты проверяют pm_project_or_404 и
lead_project_or_404. Меню лишь не показывает того, куда человека всё равно
не пустят, — права оно не выдаёт.

Пункт активен по view_name (namespace:url_name) текущего запроса, а не по
одному url_name: у разных разделов есть одноимённые страницы (list, detail).
"""
from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.conf import settings
from django.urls import NoReverseMatch, reverse

from apps.accounts.models import User

# Сколько своих проектов ПМ и тимлида показывать прямо в меню.
OWN_PROJECTS_IN_NAV = 12


@dataclass
class Link:
    label: str
    view: str
    icon: str = ''
    namespaces: tuple = ()        # раздел целиком: любая страница namespace
    views: tuple = ()             # дополнительные страницы, где пункт активен
    exclude: tuple = ()           # страницы namespace, которые принадлежат другому пункту
    query: dict = field(default_factory=dict)
    kwargs: dict = field(default_factory=dict)
    count: int = 0


@dataclass
class Group:
    label: str
    icon: str
    children: list
    namespaces: tuple = ()
    views: tuple = ()
    exclude: tuple = ()
    href: str = ''


@dataclass
class Section:
    title: str
    items: list
    empty: str = ''


def unread_notifications(request) -> int:
    """Непрочитанные уведомления руководителя — один запрос на страницу
    (их показывают и пункт меню, и колокольчик)."""
    if not hasattr(request, '_gp_unread'):
        from apps.notifications.services import unread_count

        request._gp_unread = unread_count()
    return request._gp_unread


def role_home(user) -> str:
    if user.role == User.Role.PROJECT_MANAGER:
        return 'pm_portal:dashboard'
    if user.role == User.Role.TEAM_LEAD:
        return 'lead_portal:dashboard'
    return 'dashboard'


def nav_kind(user) -> str:
    """Какое меню показывать: pm, lead, полное (head) или никакого (none).

    Полное — только руководителю, администратору и superuser; остальные
    роли в каркас и не попадут (RoleAccessMiddleware), но и меню им не
    строим — default deny.
    """
    from apps.accounts.access import has_full_access

    if user.role == User.Role.PROJECT_MANAGER:
        return 'pm'
    if user.role == User.Role.TEAM_LEAD:
        return 'lead'
    if has_full_access(user):
        return 'head'
    return 'none'


# ---------------------------------------------------------------- состав меню

def _academy(training) -> list:
    """IT-академия скрыта (11F), пока не включат ACADEMY_ENABLED."""
    if not settings.ACADEMY_ENABLED:
        return []
    children = [Link(b['label'], 'training:plan', query={'branch': b['value']}) for b in training.nav_tree()]
    return [Group('IT-академия', 'book-open', namespaces=('training',), children=children,
                  href=_safe_reverse('training:plan'))]


def _head_menu(request) -> list:
    from apps.training import selectors as training

    return [
        Link('Главная', 'dashboard', icon='home'),
        Section('Работа', [
            Link('Табель', 'attendance:dashboard', icon='clipboard-check', namespaces=('attendance', 'flows')),
            # 11F: главный рабочий раздел после запуска бота — наверху, а не в «Резерве»
            Link('Telegram-бот', 'tgbot:reconcile', icon='bot', namespaces=('tgbot',)),
            # 11F: всё про проекты и клиентов — одним пунктом
            Group('Проекты и заказчики', 'folder', namespaces=('projects', 'clients', 'documents'), children=[
                Link('Все проекты', 'projects:list'),
                Link('В процессе', 'projects:list_in_progress'),
                Link('Завершён', 'projects:list_completed'),
                Link('Отклонён', 'projects:list_rejected'),
                Link('Отчёты по проектам', 'projects:reports'),
                Link('Заказчики', 'clients:list', namespaces=('clients',)),
                Link('Заявки с брифа', 'documents:brief_requests'),
                Link('Документы', 'documents:list', namespaces=('documents',), exclude=('documents:brief_requests',)),
            ]),
            Link('Еженедельные отчёты', 'weekly_reports:board', icon='calendar', namespaces=('weekly_reports',)),
            Link('Видеособрания', 'video:list', icon='users', namespaces=('video',)),
        ]),
        # 11F: всё про людей — один раздел: стажёры, ПМ и тимлиды, выпускники и резерв
        Section('Люди', [
            Group('Стажёры', 'graduation-cap', namespaces=('interns', 'resources'),
                  exclude=('interns:graduates', 'interns:resume_bank'), children=[
                Link('Все стажёры', 'interns:list'),
                Link('По направлениям', 'resources:forecast'),
                Link('По проектам', 'interns:by_project'),
                Link('По филиалам', 'interns:by_branch'),
                Link('Ожидают проект', 'interns:waiting'),
                Link('Решения по стажёрам', 'interns:internship_requests'),
            ]),
            Group('ПМ и тимлиды', 'users', namespaces=('teams',), children=[
                Link('ПМ', 'teams:pm_list'),
                Link('Тимлиды', 'teams:lead_list'),
            ]),
            Group('Выпускники и резерв', 'briefcase', namespaces=('reserve', 'graduate_bot'),
                  views=('interns:graduates', 'interns:resume_bank'), children=[
                Link('Выпускники', 'interns:graduates'),
                Link('Кадровый резерв', 'reserve:board', namespaces=('reserve',)),
                Link('Банк резюме', 'interns:resume_bank'),
                Link('Бот-выпускник', 'graduate_bot:activity', namespaces=('graduate_bot',)),
            ]),
        ] + _academy(training)),
        Section('Контроль', [
            Link('Скрипты', 'scripts:list', icon='scroll', namespaces=('scripts',)),
            Link('Уведомления', 'notifications:list', icon='bell', namespaces=('notifications',),
                 count=unread_notifications(request)),
        ]),
    ]


def _project_links(projects, view: str) -> list:
    from apps.projects.models import ProjectStatus

    current = [p for p in projects if p.status in (ProjectStatus.ACTIVE, ProjectStatus.PAUSED)]
    current.sort(key=lambda p: p.name.lower())
    return [
        Link(project.name, view, icon='folder', kwargs={'pk': project.pk})
        for project in current[:OWN_PROJECTS_IN_NAV]
    ]


def _pm_menu(request) -> list:
    from apps.pm_portal.services import pm_projects

    return [
        Link('Мои проекты', 'pm_portal:dashboard', icon='layout-grid'),
        Link('Отчёты', 'pm_portal:weekly_reports', icon='calendar'),
        Link('Собрания', 'pm_portal:meeting_list', icon='users', views=('pm_portal:meeting_new', 'pm_portal:meeting_detail')),
        Section('В работе', _project_links(pm_projects(request.user), 'pm_portal:project_detail'),
                empty='Активных проектов нет'),
    ]


def _lead_menu(request) -> list:
    from apps.lead_portal.services import lead_projects

    return [
        Link('Мои проекты', 'lead_portal:dashboard', icon='layout-grid'),
        Link('Моё резюме', 'lead_portal:resume', icon='user'),
        Link('Отчёты и оценки', 'lead_portal:weekly_reports', icon='calendar'),
        Link('Собрания', 'lead_portal:meeting_list', icon='calendar',
             views=('lead_portal:meeting_new', 'lead_portal:meeting_detail')),
        Link('Telegram-группы', 'lead_portal:telegram_groups', icon='users'),
        Section('В работе', _project_links(lead_projects(request.user), 'lead_portal:project_detail'),
                empty='Активных проектов нет'),
    ]


MENUS = {'head': _head_menu, 'pm': _pm_menu, 'lead': _lead_menu, 'none': lambda request: []}


# ---------------------------------------------------------------- сборка

def _safe_reverse(view: str, kwargs=None) -> str:
    try:
        return reverse(view, kwargs=kwargs or None)
    except NoReverseMatch:
        return ''


class _Here:
    """Текущая страница: с чем сравниваем пункты."""

    def __init__(self, request):
        match = getattr(request, 'resolver_match', None)
        self.view = match.view_name if match else ''
        self.namespace = match.namespace if match else ''
        self.kwargs = match.kwargs if match else {}
        self.query = request.GET


def _link_active(link: Link, here: _Here) -> bool:
    if here.view in link.exclude:
        return False
    if link.query and not all(here.query.get(k) == str(v) for k, v in link.query.items()):
        return False
    if link.kwargs:
        # Пункт-проект активен на любой странице этого проекта в своём
        # разделе: карточка, отчёт, собрание, участник…
        return here.namespace == link.view.split(':')[0] and all(
            str(here.kwargs.get(k)) == str(v) for k, v in link.kwargs.items()
        )
    if link.query:
        return here.view == link.view
    return here.view == link.view or here.view in link.views or here.namespace in link.namespaces


def _href(link: Link) -> str:
    url = _safe_reverse(link.view, link.kwargs)
    if url and link.query:
        url += '?' + urlencode(link.query)
    return url


def _render_link(link: Link, here: _Here) -> dict:
    return {
        'type': 'link', 'label': link.label, 'href': _href(link), 'icon': link.icon,
        'active': _link_active(link, here), 'count': link.count,
    }


def build(request) -> dict:
    """Меню, хлебные крошки и «домой» для текущего пользователя."""
    user = request.user
    kind = nav_kind(user)
    here = _Here(request)
    items, trail = [], []

    for number, entry in enumerate(MENUS[kind](request)):
        if isinstance(entry, Section):
            items.append({'type': 'section', 'title': entry.title})
            links = entry.items
            if not links and entry.empty:
                items.append({'type': 'empty', 'text': entry.empty})
        else:
            links = [entry]
        for item in links:
            if isinstance(item, Group):
                children = [_render_link(child, here) for child in item.children]
                active_child = next((c for c in children if c['active']), None)
                active = bool(active_child) or (
                    here.view not in item.exclude
                    and (here.namespace in item.namespaces or here.view in item.views)
                )
                items.append({
                    'type': 'group', 'id': f'nav-group-{number}-{len(items)}',
                    'label': item.label, 'icon': item.icon, 'children': children,
                    'active': active, 'href': item.href or (children[0]['href'] if children else ''),
                })
                if active:
                    trail = [{'label': item.label, 'href': item.href or ''}]
                    if active_child:
                        trail.append({'label': active_child['label'], 'href': active_child['href']})
            else:
                rendered = _render_link(item, here)
                items.append(rendered)
                if rendered['active'] and not trail:
                    trail = [{'label': rendered['label'], 'href': rendered['href']}]

    home_view = role_home(user)
    home = {'label': 'Главная' if kind == 'head' else 'Мои проекты', 'href': _safe_reverse(home_view)}
    crumbs = [home] + [c for c in trail if c['href'] != home['href']]
    return {'kind': kind, 'items': items, 'crumbs': crumbs, 'home': home}

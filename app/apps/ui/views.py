"""Каталог компонентов /ui/ — внутренняя витрина дизайн-системы.

Все данные на странице вымышленные и создаются здесь же, без БД.
"""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.ui.context_processors import can_view_catalog
from apps.ui.forms import DemoForm
from apps.ui.htmx import with_toast

DEMO_PROJECTS = [
    {'name': 'Альфа CRM', 'client': 'ОсОО «Пример»', 'stage': 'Разработка', 'tone': 'primary', 'progress': 64, 'team': 7},
    {'name': 'Бета Маркет', 'client': 'ИП Тестов', 'stage': 'Тестирование', 'tone': 'info', 'progress': 82, 'team': 5},
    {'name': 'Гамма Учёт', 'client': 'ОсОО «Образец»', 'stage': 'Дизайн', 'tone': 'warning', 'progress': 21, 'team': 4},
    {'name': 'Дельта Бот', 'client': 'Фонд «Демо»', 'stage': 'Сдан', 'tone': 'success', 'progress': 100, 'team': 3},
    {'name': 'Эпсилон Портал', 'client': 'ОсОО «Макет»', 'stage': 'Приостановлен', 'tone': 'danger', 'progress': 40, 'team': 6},
    {'name': 'Зета Склад', 'client': 'ИП Примеров', 'stage': 'Разработка', 'tone': 'primary', 'progress': 55, 'team': 8},
    {'name': 'Эта Медиа', 'client': 'ОсОО «Шаблон»', 'stage': 'Аналитика', 'tone': 'neutral', 'progress': 10, 'team': 2},
    {'name': 'Тета Финанс', 'client': 'ОсОО «Каталог»', 'stage': 'Тестирование', 'tone': 'info', 'progress': 91, 'team': 5},
    {'name': 'Йота Тревел', 'client': 'ИП Демобаев', 'stage': 'Разработка', 'tone': 'primary', 'progress': 47, 'team': 6},
    {'name': 'Каппа Школа', 'client': 'Фонд «Вымысел»', 'stage': 'Дизайн', 'tone': 'warning', 'progress': 33, 'team': 4},
    {'name': 'Лямбда Клиника', 'client': 'ОсОО «Прототип»', 'stage': 'Сдан', 'tone': 'success', 'progress': 100, 'team': 9},
    {'name': 'Мю Логистика', 'client': 'ИП Стабов', 'stage': 'Разработка', 'tone': 'primary', 'progress': 72, 'team': 7},
]
SORTABLE = {'name', 'client', 'progress', 'team'}
TABLE_COLUMNS = [
    {'key': 'name', 'label': 'Проект', 'sort': 'name', 'primary': True},
    {'key': 'client', 'label': 'Заказчик', 'sort': 'client'},
    {'key': 'stage', 'label': 'Этап'},
    {'key': 'progress', 'label': 'Прогресс', 'sort': 'progress', 'numeric': True},
    {'key': 'team', 'label': 'Команда', 'sort': 'team', 'numeric': True},
]
TABS = [
    {'key': 'overview', 'label': 'Обзор'},
    {'key': 'team', 'label': 'Команда', 'count': 7},
    {'key': 'documents', 'label': 'Документы', 'count': 3},
    {'key': 'history', 'label': 'История'},
]


def _check(request):
    from django.conf import settings
    from django.http import Http404

    if settings.GP2_ENVIRONMENT == 'production':
        raise Http404  # в production каталога нет вовсе
    if not can_view_catalog(request.user):
        raise PermissionDenied


def _table_page(request):
    sort = request.GET.get('sort', 'name')
    key = sort.lstrip('-')
    rows = DEMO_PROJECTS
    if key in SORTABLE:
        rows = sorted(rows, key=lambda r: (r[key].lower() if isinstance(r[key], str) else r[key]),
                      reverse=sort.startswith('-'))
    page = Paginator(rows, 5).get_page(request.GET.get('page'))
    table_rows = [{
        'name': {'text': row['name'], 'secondary': f"Команда: {row['team']} чел."},
        'client': row['client'],
        'stage': {'badge': row['stage'], 'tone': row['tone']},
        'progress': f"{row['progress']}%",
        'team': row['team'],
    } for row in page.object_list]
    return page, table_rows


@login_required
def catalog(request):
    _check(request)
    page, table_rows = _table_page(request)
    context = {'columns': TABLE_COLUMNS, 'table_rows': table_rows, 'page_obj': page}

    if request.htmx and request.htmx.target == 'demo-table':
        return render(request, 'ui/_demo_table.html', context)

    tab = request.GET.get('tab', 'overview')
    tabs = [dict(t, href=f'?tab={t["key"]}#tabs') for t in TABS]
    if request.htmx and request.htmx.target == 'demo-tab-panel':
        return render(request, 'ui/_demo_tab.html', {'tab': tab, 'tabs': tabs})

    invalid = request.GET.get('errors') == '1'
    form = DemoForm({'name': 'A', 'email': 'не-email', 'priority': 'normal'}) if invalid else DemoForm()
    if invalid:
        form.is_valid()
    context.update({
        'form': form, 'filter_form': DemoForm(prefix='filters'), 'invalid': invalid, 'tab': tab, 'tabs': tabs,
        'icons': ICONS,
        'colors': COLORS,
        'crumbs': [{'label': 'Главная', 'href': '/'}, {'label': 'Компоненты UI', 'href': ''}],
        'demo_crumbs': [{'label': 'Проекты', 'href': '#'}, {'label': 'Альфа CRM', 'href': '#'}, {'label': 'Команда', 'href': ''}],
    })
    return render(request, 'ui/catalog.html', context)


@login_required
@require_POST
def catalog_toast(request):
    """Пример серверного тоста для htmx: ответ без тела + HX-Trigger."""
    _check(request)
    return with_toast(HttpResponse(status=204), 'Изменения сохранены (ответ сервера).', 'success')


ICONS = [
    'home', 'layout-grid', 'folder', 'briefcase', 'inbox', 'file-text', 'clipboard-check', 'graduation-cap',
    'book-open', 'users', 'user', 'clock', 'calendar', 'bell', 'bot', 'scroll', 'search', 'filter', 'plus',
    'pencil', 'trash', 'upload', 'external-link', 'check', 'x', 'menu', 'more-horizontal', 'chevron-down',
    'chevron-right', 'arrow-up-down', 'info', 'check-circle', 'alert-triangle', 'alert-circle', 'sun', 'moon',
    'monitor', 'palette', 'eye', 'log-out',
]
COLORS = [
    ('Фон', '--color-bg'), ('Поверхность', '--color-surface'), ('Утопленная', '--color-surface-sunken'),
    ('Граница', '--color-border'), ('Текст', '--color-text'), ('Вторичный', '--color-text-muted'),
    ('Акцент', '--color-primary'), ('Успех', '--color-success'), ('Внимание', '--color-warning'),
    ('Ошибка', '--color-danger'), ('Инфо', '--color-info'),
]

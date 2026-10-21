"""Компоненты дизайн-системы 2.0 как теги шаблонов.

    {% load ui %}
    {% ui_button "Сохранить" variant="primary" type="submit" icon="check" %}
    {% ui_badge "Активный" tone="success" %}
    {% ui_field form.email %}
    {% ui_card title="Команда" %} … {% ui_slot "actions" %}…{% end_ui_slot %} {% end_ui_card %}

Все значения экранируются. Дополнительные атрибуты передаются только с
известными префиксами (hx_*, data_*, aria_*, x_*), подчёркивания становятся
дефисами: hx_post="/x/" → hx-post="/x/", x_on_click="open = true" →
x-on:click="…". Так через параметры тега нельзя подставить разметку.
Живые примеры всех компонентов — на странице /ui/.
"""
from django import forms, template
from django.template import loader
from django.template.base import token_kwargs
from django.templatetags.static import static
from django.utils.html import conditional_escape, format_html, format_html_join
from django.utils.safestring import mark_safe

register = template.Library()

TONES = ('neutral', 'primary', 'success', 'warning', 'danger', 'info')
LEVEL_ICONS = {'info': 'info', 'success': 'check-circle', 'warning': 'alert-triangle', 'danger': 'alert-circle'}
MESSAGE_LEVELS = {'error': 'danger', 'danger': 'danger', 'warning': 'warning', 'success': 'success', 'info': 'info', 'debug': 'info'}
PASSTHROUGH = ('hx_', 'data_', 'aria_', 'x_')
PLAIN_ATTRS = ('id', 'name', 'value', 'form', 'title', 'target', 'rel', 'role', 'tabindex', 'download', 'autofocus')


# ------------------------------------------------------------ helpers

def _attr_name(key: str) -> str:
    if key.startswith('x_on_'):
        return 'x-on:' + key[5:].replace('_', '-')
    if key.startswith('x_bind_'):
        return 'x-bind:' + key[7:].replace('_', '-')
    return key.replace('_', '-')


def extra_attrs(attrs: dict) -> str:
    """Безопасные дополнительные атрибуты из kwargs тега."""
    pairs = []
    for key, value in attrs.items():
        if value is None or value is False:
            continue
        if key.startswith(PASSTHROUGH) or key in PLAIN_ATTRS:
            name = _attr_name(key)
            pairs.append((name, '' if value is True else value))
    return format_html_join('', ' {}="{}"', pairs)


def classes(*names) -> str:
    return ' '.join(n for n in names if n)


def icon(name: str, size: str = '', extra: str = '') -> str:
    if not name:
        return ''
    return format_html(
        '<svg class="{}" aria-hidden="true" focusable="false"><use href="{}#{}"></use></svg>',
        classes('gp-icon', f'gp-icon--{size}' if size else '', extra), static('gp2/icons.svg'), name,
    )


def tone_of(value: str) -> str:
    value = MESSAGE_LEVELS.get(value, value)
    return value if value in TONES else 'neutral'


def initials(name: str) -> str:
    words = [w for w in str(name or '').replace('-', ' ').split() if w[:1].isalpha()]
    return ''.join(w[0] for w in words[:2]).upper() or '?'


# ------------------------------------------------------------ простые компоненты

@register.simple_tag
def ui_icon(name, size='', label='', extra=''):
    """Иконка из спрайта. label — для самостоятельной иконки со смыслом
    (без него она скрыта от скринридеров); extra — дополнительный класс."""
    if label:
        return format_html(
            '<svg class="{}" role="img" aria-label="{}"><use href="{}#{}"></use></svg>',
            classes('gp-icon', f'gp-icon--{size}' if size else '', extra), label, static('gp2/icons.svg'), name,
        )
    return icon(name, size, extra)


@register.simple_tag
def ui_button(label='', variant='secondary', size='', href='', type='button', icon_name='',
              icon_end='', block=False, disabled=False, busy=False, label_hidden=False, **attrs):
    """Кнопка или ссылка-кнопка. icon_name — иконка слева, icon_end — справа.
    label_hidden — кнопка-иконка: подпись уходит в aria-label."""
    icon_only = label_hidden and icon_name
    css = classes(
        'gp-btn', f'gp-btn--{variant}', f'gp-btn--{size}' if size else '',
        'gp-btn--icon' if icon_only else '', 'gp-btn--block' if block else '',
        attrs.pop('class_', ''),
    )
    inner = format_html(
        '{}{}{}',
        icon(icon_name, 'sm' if size == 'sm' else ''),
        '' if icon_only else format_html('<span>{}</span>', label),
        icon(icon_end, 'sm'),
    )
    common = format_html(
        '{}{}{}',
        format_html(' aria-label="{}"', label) if icon_only else '',
        mark_safe(' aria-busy="true"') if busy else '',
        extra_attrs(attrs),
    )
    if href and not disabled:
        return format_html('<a href="{}" class="{}"{}>{}</a>', href, css, common, inner)
    return format_html(
        '<button type="{}" class="{}"{}{}>{}</button>',
        type, css, mark_safe(' disabled') if disabled else '', common, inner,
    )


@register.simple_tag
def ui_badge(text, tone='neutral', dot=False):
    return format_html(
        '<span class="{}">{}</span>',
        classes('gp-badge', f'gp-badge--{tone_of(tone)}', 'gp-badge--dot' if dot else ''), text,
    )


@register.simple_tag
def ui_alert(text='', level='info', title=''):
    level = tone_of(level) if tone_of(level) in LEVEL_ICONS else 'info'
    return format_html(
        '<div class="gp-alert gp-alert--{}" role="{}">{}<div class="gp-alert__body">{}{}</div></div>',
        level, 'alert' if level == 'danger' else 'status', icon(LEVEL_ICONS[level]),
        format_html('<p class="gp-alert__title">{}</p>', title) if title else '',
        format_html('<p class="gp-alert__text">{}</p>', text) if text else '',
    )


@register.simple_tag
def ui_avatar(name, size=''):
    return format_html(
        '<span class="{}" aria-hidden="true">{}</span>',
        classes('gp-avatar', f'gp-avatar--{size}' if size else ''), initials(name),
    )


@register.filter
def ui_initials(name):
    return initials(name)


@register.simple_tag
def ui_skeleton(variant='text', lines=3, width=''):
    style = format_html(' style="width: {}"', width) if width else ''
    if variant == 'text':
        rows = format_html_join('', '<span class="gp-skeleton gp-skeleton--text"></span>', [()] * int(lines))
        return format_html('<div class="gp-skeleton-group" aria-hidden="true">{}</div>', rows)
    return format_html('<span class="gp-skeleton gp-skeleton--{}"{} aria-hidden="true"></span>', variant, style)


@register.simple_tag
def ui_loading(label='Загрузка…', inline=False, htmx=False, id=''):
    """Индикатор загрузки. htmx=True — виден только во время htmx-запроса
    (повесьте hx-indicator="#id" на элемент, который делает запрос)."""
    return format_html(
        '<div class="{}"{} role="status"><span class="gp-spinner" aria-hidden="true"></span><span>{}</span></div>',
        classes('gp-loading', 'gp-loading--inline' if inline else '', 'htmx-indicator' if htmx else ''),
        format_html(' id="{}"', id) if id else '', label,
    )


@register.inclusion_tag('components/empty.html')
def ui_empty(title, text='', icon_name='inbox', action_label='', action_href=''):
    return {'title': title, 'text': text, 'icon': icon_name,
            'action_label': action_label, 'action_href': action_href}


@register.inclusion_tag('components/breadcrumbs.html')
def ui_breadcrumbs(items):
    """items — список словарей {label, href} или пар (label, href);
    последний пункт — текущая страница."""
    crumbs = []
    for item in items or []:
        if isinstance(item, dict):
            crumbs.append({'label': item.get('label', ''), 'href': item.get('href', '')})
        else:
            label, href = (list(item) + [''])[:2]
            crumbs.append({'label': label, 'href': href})
    return {'crumbs': crumbs}


@register.simple_tag
def ui_menu_item(label, href='', icon_name='', danger=False, checked=None, type='button', **attrs):
    """Пункт выпадающего меню: ссылка (href) или кнопка."""
    css = classes('gp-menu__item', 'gp-menu__item--danger' if danger else '')
    role = 'menuitemradio' if checked is not None else 'menuitem'
    checked_attr = format_html(' aria-checked="{}"', 'true' if checked else 'false') if checked is not None else ''
    inner = format_html('{}<span>{}</span>', icon(icon_name, 'sm'), label)
    if href:
        return format_html('<a href="{}" class="{}" role="{}"{}{}>{}</a>', href, css, role, checked_attr, extra_attrs(attrs), inner)
    return format_html('<button type="{}" class="{}" role="{}"{}{}>{}</button>', type, css, role, checked_attr, extra_attrs(attrs), inner)


# ------------------------------------------------------------ Tabs, Pagination, сортировка

@register.inclusion_tag('components/tabs.html')
def ui_tabs(items, active='', label='Разделы', hx_target=''):
    """Вкладки-ссылки. items — словари {key, label, href, count}.
    С hx_target вкладка подгружает содержимое через htmx и меняет URL."""
    tabs = [dict(item, is_active=str(item.get('key')) == str(active)) for item in items or []]
    return {'tabs': tabs, 'label': label, 'hx_target': hx_target}


def _query_with(request, **changes) -> str:
    params = request.GET.copy() if request else None
    if params is None:
        from django.http import QueryDict
        params = QueryDict(mutable=True)
    for key, value in changes.items():
        if value in (None, ''):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return '?' + encoded if encoded else '?'


@register.inclusion_tag('components/pagination.html', takes_context=True)
def ui_pagination(context, page, param='page', hx_target='', label='объектов'):
    """Пагинация по объекту Page. Остальные GET-параметры (фильтры,
    сортировка) сохраняются."""
    request = context.get('request')
    if page is None or page.paginator.num_pages <= 1:
        return {'show': False}
    current, last = page.number, page.paginator.num_pages
    window = sorted({1, last, *range(max(1, current - 1), min(last, current + 1) + 1)})
    pages, previous = [], 0
    for number in window:
        if number - previous > 1:
            pages.append({'gap': True})
        pages.append({'number': number, 'href': _query_with(request, **{param: number}), 'current': number == current})
        previous = number
    return {
        'show': True, 'page': page, 'pages': pages, 'hx_target': hx_target, 'label': label,
        'prev_href': _query_with(request, **{param: page.previous_page_number()}) if page.has_previous() else '',
        'next_href': _query_with(request, **{param: page.next_page_number()}) if page.has_next() else '',
    }


@register.simple_tag(takes_context=True)
def ui_sort(context, label, field, param='sort', hx_target=''):
    """Заголовок сортируемой колонки: field → -field → field. Номер
    страницы сбрасывается, остальные параметры сохраняются."""
    request = context.get('request')
    current = request.GET.get(param, '') if request else ''
    is_sorted = current.lstrip('-') == field
    descending = current.startswith('-')
    next_value = f'-{field}' if is_sorted and not descending else field
    href = _query_with(request, **{param: next_value, 'page': None})
    icon_name = ('arrow-down' if descending else 'arrow-up') if is_sorted else 'arrow-up-down'
    state = ('по убыванию' if descending else 'по возрастанию') if is_sorted else 'не отсортировано'
    hx = format_html(' hx-get="{}" hx-target="{}" hx-push-url="true"', href, hx_target) if hx_target else ''
    return format_html(
        '<a href="{}" class="{}"{}>{}{}<span class="gp-sr-only">, {}</span></a>',
        href, classes('gp-table__sort', 'is-sorted' if is_sorted else ''), hx, label, icon(icon_name), state,
    )


@register.inclusion_tag('components/datatable.html', takes_context=True)
def ui_datatable(context, columns, rows, empty_title='Нет данных', empty_text='', caption='',
                 hx_target='', stack=True):
    """Простая таблица из данных. columns — словари {key, label, sort,
    numeric, primary}; rows — словари, значение ячейки — строка или
    {text, href, secondary, badge, tone}. Для сложных таблиц используйте
    разметку .gp-table напрямую (см. /ui/)."""
    body = []
    for row in rows or []:
        cells = []
        for column in columns:
            value = row.get(column['key'], '')
            cell = value if isinstance(value, dict) else {'text': value}
            cells.append(dict(cell, column=column))
        body.append(cells)
    return {
        'columns': columns, 'rows': body, 'empty_title': empty_title, 'empty_text': empty_text,
        'caption': caption, 'hx_target': hx_target, 'stack': stack, 'request': context.get('request'),
    }


# ------------------------------------------------------------ формы

def _widget_kind(field) -> str:
    widget = field.field.widget
    if isinstance(widget, forms.CheckboxInput):
        return 'checkbox'
    if isinstance(widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)):
        return 'choices'
    if isinstance(widget, forms.Select):
        return 'select'
    if isinstance(widget, forms.Textarea):
        return 'textarea'
    if isinstance(widget, forms.HiddenInput):
        return 'hidden'
    return 'input'


WIDGET_CLASSES = {'select': 'gp-select', 'textarea': 'gp-textarea', 'input': 'gp-input'}


@register.inclusion_tag('components/field.html')
def ui_field(field, wide=False, help_text='', label=''):
    """Поле Django-формы в разметке дизайн-системы: подпись, подсказка,
    ошибки, aria-атрибуты. Формы и виджеты в views не меняются."""
    kind = _widget_kind(field)
    describedby = []
    if field.help_text or help_text:
        describedby.append(f'{field.auto_id}_help')
    if field.errors:
        describedby.append(f'{field.auto_id}_error')
    attrs = {}
    if kind in WIDGET_CLASSES:
        existing = field.field.widget.attrs.get('class', '')
        attrs['class'] = classes(WIDGET_CLASSES[kind], existing)
    if field.errors:
        attrs['aria-invalid'] = 'true'
    if describedby:
        attrs['aria-describedby'] = ' '.join(describedby)
    choices = []
    if kind == 'choices':
        for option in field:
            choices.append(option)
    return {
        'field': field, 'kind': kind, 'widget': field.as_widget(attrs=attrs) if kind != 'choices' else '',
        'choices': choices, 'wide': wide, 'help': help_text or field.help_text,
        'label': label or field.label, 'error_icon': icon('alert-circle'),
    }


@register.inclusion_tag('components/form.html')
def ui_form(form, cols=1):
    """Все поля формы подряд: ошибки формы, видимые поля, скрытые поля."""
    return {'form': form, 'cols': cols}


# ------------------------------------------------------------ блочные компоненты со слотами

class SlotNode(template.Node):
    """{% ui_slot "actions" %}…{% end_ui_slot %} внутри блочного компонента."""

    def __init__(self, name, nodelist):
        self.name, self.nodelist = name, nodelist

    def render(self, context):
        slots = context.get('_ui_slots')
        if slots is not None:
            slots[self.name.resolve(context)] = self.nodelist.render(context)
        return ''


@register.tag('ui_slot')
def do_slot(parser, token):
    bits = token.split_contents()
    if len(bits) != 2:
        raise template.TemplateSyntaxError('ui_slot принимает одно имя: {% ui_slot "actions" %}')
    nodelist = parser.parse(('end_ui_slot',))
    parser.delete_first_token()
    return SlotNode(parser.compile_filter(bits[1]), nodelist)


class BlockComponentNode(template.Node):
    def __init__(self, template_name, kwargs, nodelist):
        self.template_name, self.kwargs, self.nodelist = template_name, kwargs, nodelist

    def render(self, context):
        values = {key: value.resolve(context) for key, value in self.kwargs.items()}
        with context.push(_ui_slots={}):
            body = self.nodelist.render(context)
            slots = context['_ui_slots']
        values.update(body=mark_safe(body), slots={k: mark_safe(v) for k, v in slots.items()})
        # Тело уже отрендерено в контексте страницы (csrf_token, request там
        # есть); шаблону компонента нужны только его параметры.
        return loader.get_template(self.template_name).render(values)


def block_component(name, template_name):
    def compile_tag(parser, token):
        bits = token.split_contents()[1:]
        kwargs = token_kwargs(bits, parser, support_legacy=False)
        if bits:
            raise template.TemplateSyntaxError(f'{name}: параметры только в виде key=value')
        nodelist = parser.parse((f'end_{name}',))
        parser.delete_first_token()
        return BlockComponentNode(template_name, kwargs, nodelist)

    register.tag(name, compile_tag)


block_component('ui_card', 'components/card.html')
block_component('ui_page_header', 'components/page_header.html')
block_component('ui_modal', 'components/modal.html')
block_component('ui_drawer', 'components/drawer.html')
block_component('ui_dropdown', 'components/dropdown.html')


# ------------------------------------------------------------ сообщения → тосты

@register.simple_tag
def ui_toast(message, level='info'):
    level = tone_of(level) if tone_of(level) in LEVEL_ICONS else 'info'
    return format_html(
        '<div class="gp-toast gp-toast--{}" role="{}" data-level="{}">{}'
        '<div class="gp-toast__text">{}</div>'
        '<button type="button" class="gp-btn gp-btn--ghost gp-btn--icon gp-btn--sm" data-gp-toast-close aria-label="Закрыть">{}</button></div>',
        level, 'alert' if level == 'danger' else 'status', level, icon(LEVEL_ICONS[level]),
        message, icon('x', 'sm'),
    )


@register.filter
def ui_message_level(message):
    """Уровень Django-сообщения для тоста (error → danger)."""
    return MESSAGE_LEVELS.get(getattr(message, 'level_tag', '') or 'info', 'info')


@register.filter
def ui_escape(value):
    return conditional_escape(value)


# ------------------------------------------------------------ значения показателей

@register.simple_tag
def ui_value(value, suffix='', empty='Нет данных'):
    """Значение показателя: None — это «нет данных», а не ноль."""
    if value is None:
        return format_html('<span class="gp-nodata">{}</span>', empty)
    return format_html('{}{}', value, suffix)


@register.simple_tag
def ui_delta(delta, percent=None):
    """Изменение: +3 (+12,5 %). Без процента, если база равна нулю."""
    if delta is None:
        return format_html('<span class="gp-nodata">—</span>')
    tone = 'up' if delta > 0 else 'down' if delta < 0 else 'flat'
    sign = '+' if delta > 0 else ''
    pct = ''
    if percent is not None:
        pct_text = '0' if percent == 0 else f'{percent:+.1f}'.replace('.', ',')
        pct = format_html(' <span class="gp-muted">({} %)</span>', pct_text)
    return format_html('<span class="gp-delta gp-delta--{}">{}{}</span>{}', tone, sign, delta, pct)

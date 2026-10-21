# Дизайн-система GeeksPro Control 2.0

Интерфейс 2.0 — новый SaaS-интерфейс, не доработка 1.0. Технологическая
основа остаётся (Django Templates, HTMX, Alpine.js), визуальный слой
создаётся заново. Старые `app.css`, `graphics.css` и прочие стили 1.0
удаляются по мере перевода модулей и в новые экраны не подключаются.

## Принципы

1. **Mobile-first.** Сначала макет 360px, затем планшет и десктоп. Горизонтальной прокрутки страницы нет ни на одной ширине; широкие таблицы прокручиваются внутри своего контейнера или превращаются в карточки.
2. **Только токены.** Цвета, отступы, радиусы, тени, шрифты — `var(--…)` из `static/gp2/tokens.css`; сырые hex/px в компонентах запрещены. Акцентный текст (ссылки, выделения) — `--color-link`, фон кнопок — `--color-primary`: в тёмной теме это разные оттенки, чтобы оба держали контраст ≥ 4.5:1.
3. **Одна реализация компонента.** Кнопка, поле, таблица, карточка существуют в одном партиале и одном CSS-блоке; страницы их только собирают.
4. **Светлая и тёмная темы** на одних токенах: по системе, с ручным переключателем (`data-theme` на `<html>`, выбор в `localStorage`).
5. **Доступность:** контраст текста ≥ 4.5:1, видимый фокус (`--color-focus`), цели касания ≥ 44px, управление с клавиатуры, `prefers-reduced-motion`.

## Слои CSS

Файлы в `app/static/gp2/`, порядок слоёв объявлен в `tokens.css`:
`@layer legacy, tokens, base, layout, components, utilities`.

| Слой | Файл | Что внутри |
|---|---|---|
| `tokens` | `tokens.css` | бренд (`--brand-*`, временный), цвета светлой и тёмной тем, типографика, отступы, радиусы, тени, z-index |
| `base` | `base.css` | reset, шрифт, фокус, reduced motion — без оформления заголовков и ссылок |
| `layout` | `layout.css` | каркас: sidebar / рейка / выезжающая панель, topbar, контент |
| `components` | `components.css` | все компоненты, классы с префиксом `gp-` |
| `utilities` | `utilities.css` | раскладка (`gp-stack`, `gp-cluster`, `gp-grid`), состояния каркаса |
| `legacy` | `legacy.css` | стили 1.0 для непереведённых страниц — самый нижний слой |

Плюс `icons.svg` (SVG-спрайт) и `app.js` (тема, каркас, меню, окна, тосты, htmx).
Сборщика и npm нет.

## Как пользоваться

Каталог всех компонентов с живыми примерами — **`/ui/`** (руководитель, администратор, staff).

```django
{% extends "layouts/app.html" %}      {# новая страница; старые наследуют base.html #}
{% load ui %}
{% block title %}Проекты — GeeksPro Control{% endblock %}
{% block page %}
  {% ui_page_header title="Проекты" subtitle="Активные и завершённые" %}
    {% ui_button "Новый проект" variant="primary" icon_name="plus" href=create_url %}
  {% end_ui_page_header %}

  {% ui_card title="Фильтры" %}
    {% ui_slot "actions" %}{% ui_button "Сбросить" variant="ghost" size="sm" %}{% end_ui_slot %}
    <form class="gp-form">{% ui_form form cols=2 %}</form>
  {% end_ui_card %}

  {% ui_datatable columns rows hx_target="#projects" %}
  {% ui_pagination page_obj hx_target="#projects" %}
{% endblock %}
```

| Тег | Назначение |
|---|---|
| `ui_button` | кнопка / ссылка-кнопка: `variant` primary, secondary, ghost, danger, danger-soft, link; `size` sm/lg; `icon_name`, `icon_end`, `busy`, `disabled`, `label_hidden` |
| `ui_field`, `ui_form` | любые поля Django-форм (input, select, textarea, checkbox, radio, файлы) с подписью, подсказкой, ошибкой и aria |
| `ui_badge`, `ui_alert`, `ui_avatar`, `ui_icon` | статусы (тоны neutral, primary, success, warning, danger, info), сообщения, инициалы, иконки |
| `ui_card`, `ui_page_header` | блочные компоненты со слотами `{% ui_slot "actions" %}`, `{% ui_slot "footer" %}` |
| `ui_modal`, `ui_drawer` | окна на `<dialog>`; открываются `data_gp_open="id"`, закрываются `data_gp_close` |
| `ui_dropdown` + `ui_menu_item` | выпадающее меню с клавиатурой |
| `ui_tabs` | вкладки-ссылки, с `hx_target` — подгрузка через htmx |
| `ui_datatable`, `ui_sort`, `ui_pagination` | таблица из данных или своя разметка `.gp-table`; на телефоне — карточки |
| `ui_skeleton`, `ui_loading`, `ui_empty`, `ui_breadcrumbs` | состояния загрузки и пустоты, навигационная цепочка |

Безопасность: все значения экранируются; дополнительные атрибуты принимаются
только с префиксами `hx_`, `data_`, `aria_`, `x_` (`x_on_click` → `x-on:click`).
Строковые литералы в самом шаблоне Django считает доверенными — пользовательские
данные передавайте переменными.

Тост из htmx-ответа: `apps.ui.htmx.with_toast(response, "Сохранено")`.

## Единый каркас

`layouts/app.html` — одно меню на все роли (`apps/ui/navigation.py`): руководитель
видит все разделы, ПМ — «Мои проекты» и свои активные проекты, тимлид — проекты,
резюме. Права по-прежнему проверяет сервер (middleware порталов и
`pm_project_or_404` / `lead_project_or_404`); меню их не расширяет.

Непереведённые страницы (`base.html`, порталы ПМ и тимлида) уже работают в новом
каркасе, их содержимое — в светлой legacy-панели со стилями 1.0. Тёмная тема
применяется к каркасу и переведённым страницам.

## Брейкпоинты

| Имя | Ширина | Каркас |
|---|---|---|
| mobile | < 768px | топбар + нижняя навигация или drawer, одна колонка, gutter 16px |
| tablet | 768–1279px | свёрнутый сайдбар (иконки), 1–2 колонки, gutter 24px |
| desktop | ≥ 1280px | полный сайдбар 248px, до 12 колонок, контент до 1440px, gutter 32px |

Компоненты по возможности реагируют на ширину своего контейнера (`@container`), а не окна.

## Компоненты (план)

- **Каркас:** `layouts/app.html` (сайдбар, топбар с поиском, профилем и переключателем темы), `layouts/public.html` (публичные формы: бриф, анкеты).
- **Действия:** Button (primary / secondary / ghost / danger, размеры sm/md, состояние загрузки для HTMX), IconButton, Dropdown.
- **Формы:** Field (label, подсказка, ошибка), Input, Select, Textarea, Checkbox/Radio/Switch, DatePicker, FileUpload. Поля на всю ширину на телефоне, две колонки на десктопе.
- **Данные:** DataTable (сортировка, фильтры, пагинация через HTMX, липкая шапка, на телефоне — карточки), StatCard/KPI, Badge/StatusPill, Avatar, ProgressBar, Timeline.
- **Обратная связь:** Toast, Alert, Modal/Drawer (заменяет `confirm-modal.js`), EmptyState, Skeleton.
- **Навигация:** Tabs, Breadcrumbs, Pagination, Stepper (этапы проекта).

Компонент = партиал `templates/components/<name>.html` + CSS-блок
`components/<name>.css` + пример на служебной странице `/ui/` (каталог компонентов,
доступен только staff).

## Порядок перевода модулей

1. Основа: `base.css`, `layout.css`, каркас, страница `/ui/`, переключатель темы.
2. Dashboard — первый экран на новой системе.
3. Проекты → Команды и стажёры → Резерв → Документы → Собрания и посещаемость → Отчёты → порталы PM и тимлида → публичные формы.

Каждый модуль переводится целиком: новые шаблоны вместо старых, views и сервисы
сохраняются, где возможно. Проверка — на 360, 768, 1280 и 1440px в обеих темах.

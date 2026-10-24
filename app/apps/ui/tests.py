"""Тесты дизайн-системы 2.0: меню по ролям, каркас, каталог, компоненты."""
import json

from django import forms
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.template import Context, Template
from django.template.loader import get_template
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.interns.models import Intern
from apps.projects.models import Project, ProjectStatus
from apps.teams.models import TeamMember, TeamRole

UserModel = get_user_model()


def render(source, context=None, request=None):
    ctx = dict(context or {})
    if request is not None:
        ctx['request'] = request
    return Template('{% load ui %}' + source).render(Context(ctx))


def nav_labels(response):
    return [
        item.get('label') or item.get('title')
        for item in response.context['ui_nav']['items']
    ]


class PortalUsersMixin:
    def make_portal_user(self, username, role, team_role, *projects):
        user = UserModel.objects.create_user(username=username, password='x', role=role)
        card = Intern.objects.create(full_name=f'Тестов {username}', user=user)
        for project in projects:
            TeamMember.objects.create(project=project, intern=card, role=team_role, status=TeamMember.Status.ACTIVE)
        return user


class HeadNavigationTests(TestCase):
    def setUp(self):
        self.head = UserModel.objects.create_user(username='head-ui', password='x', role=User.Role.HEAD)
        self.client.force_login(self.head)

    def test_head_sees_all_sections(self):
        response = self.client.get(reverse('dashboard'))
        labels = nav_labels(response)
        for expected in ('Главная', 'Работа', 'Проекты и заказчики', 'Telegram-бот', 'Люди', 'Стажёры', 'Тимлиды', 'Контроль', 'Уведомления'):
            self.assertIn(expected, labels)
        for gone in ('IT-академия', 'Резерв'):
            self.assertNotIn(gone, labels)

    def test_active_item_and_breadcrumbs_follow_view_name(self):
        response = self.client.get(reverse('projects:list'))
        nav = response.context['ui_nav']
        projects = next(i for i in nav['items'] if i.get('label') == 'Проекты и заказчики')
        self.assertTrue(projects['active'])
        self.assertEqual([c['label'] for c in projects['children'] if c['active']], ['Все проекты'])
        # «Все стажёры» тоже называется list, но в другом разделе — не активен
        interns = next(i for i in nav['items'] if i.get('label') == 'Стажёры')
        self.assertFalse(interns['active'])
        self.assertEqual([c['label'] for c in nav['crumbs']], ['Главная', 'Проекты и заказчики', 'Все проекты'])

    def test_pm_under_interns_reserve_under_leads(self):
        items = self.client.get(reverse('teams:pm_list')).context['ui_nav']['items']
        group = {i['label']: i for i in items if i.get('type') == 'group'}
        self.assertTrue(group['Стажёры']['active'])
        self.assertFalse(group['Тимлиды']['active'])
        items = self.client.get(reverse('reserve:board')).context['ui_nav']['items']
        group = {i['label']: i for i in items if i.get('type') == 'group'}
        self.assertTrue(group['Тимлиды']['active'])
        self.assertFalse(group['Стажёры']['active'])

    def test_brief_requests_do_not_highlight_documents(self):
        response = self.client.get(reverse('documents:brief_requests'))
        group = next(i for i in response.context['ui_nav']['items'] if i.get('label') == 'Проекты и заказчики')
        # 11F: заявки с брифа — внутри «Проекты и заказчики»; «Документы» при этом не подсвечены
        self.assertEqual([c['label'] for c in group['children'] if c['active']], ['Заявки с брифа'])

    def test_shell_wraps_legacy_page(self):
        # Главная, проекты и стажёры уже на 2.0 — берём страницу, ещё живущую на стилях 1.0
        response = self.client.get(reverse('interns:reserve'))
        html = response.content.decode()
        self.assertIn('class="gp-shell"', html)
        self.assertIn('class="gp-legacy"', html)
        self.assertIn('gp2/legacy.css', html)
        self.assertNotIn('sidebar__nav', html)  # старого сайдбара 1.0 больше нет
        self.assertIn('gp-topbar', html)

    def test_messages_become_toasts(self):
        from django.contrib import messages

        response = self.client.get(reverse('dashboard'))
        request = response.wsgi_request
        messages.success(request, 'Сохранено <b>!</b>')
        html = get_template('layouts/app.html').render({'messages': messages.get_messages(request)}, request)
        self.assertIn('gp-toast gp-toast--success', html)
        self.assertIn('Сохранено &lt;b&gt;!&lt;/b&gt;', html)

    def test_bell_shows_unread_count_for_head(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.context['ui_bell']['href'], reverse('notifications:list'))


class PortalNavigationTests(PortalUsersMixin, TestCase):
    def setUp(self):
        self.own = Project.objects.create(name='Свой проект')
        self.done = Project.objects.create(name='Сданный проект', status=ProjectStatus.COMPLETED)
        self.foreign = Project.objects.create(name='Чужой проект')

    def test_pm_menu_has_only_own_active_projects(self):
        pm = self.make_portal_user('pm-ui', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.own, self.done)
        self.client.force_login(pm)
        response = self.client.get(reverse('pm_portal:dashboard'))
        labels = nav_labels(response)
        self.assertEqual(labels[0], 'Мои проекты')
        self.assertIn('Свой проект', labels)
        self.assertNotIn('Чужой проект', labels)
        self.assertNotIn('Сданный проект', labels)  # в меню — только текущая работа
        for head_only in ('Заказчики', 'Стажёры', 'Уведомления', 'Тимлиды'):
            self.assertNotIn(head_only, labels)
        self.assertEqual(response.context['ui_bell'], {})
        self.assertNotIn(reverse('clients:list'), response.content.decode())

    def test_pm_project_link_is_active_on_its_pages(self):
        pm = self.make_portal_user('pm-ui2', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.own)
        self.client.force_login(pm)
        response = self.client.get(reverse('pm_portal:project_detail', args=[self.own.pk]))
        active = [i['label'] for i in response.context['ui_nav']['items'] if i.get('active')]
        self.assertEqual(active, ['Свой проект'])

    def test_pm_still_cannot_open_other_sections(self):
        """Единый каркас не расширяет права: middleware по-прежнему уводит ПМ."""
        pm = self.make_portal_user('pm-ui3', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER, self.own)
        self.client.force_login(pm)
        for url in (reverse('dashboard'), reverse('projects:list'), reverse('ui:catalog')):
            response = self.client.get(url)
            self.assertRedirects(response, reverse('pm_portal:dashboard'), fetch_redirect_response=False)
        response = self.client.get(reverse('pm_portal:project_detail', args=[self.foreign.pk]))
        self.assertEqual(response.status_code, 404)

    def test_lead_menu(self):
        lead = self.make_portal_user('lead-ui', User.Role.TEAM_LEAD, TeamRole.TEAM_LEAD, self.own)
        self.client.force_login(lead)
        response = self.client.get(reverse('lead_portal:dashboard'))
        labels = nav_labels(response)
        self.assertEqual(labels[:2], ['Мои проекты', 'Моё резюме'])
        self.assertIn('Свой проект', labels)
        self.assertNotIn('Чужой проект', labels)
        self.assertTrue(response.context['ui_bell']['href'].startswith(reverse('lead_portal:dashboard')))

    def test_empty_portal_menu_says_so(self):
        pm = self.make_portal_user('pm-ui4', User.Role.PROJECT_MANAGER, TeamRole.PROJECT_MANAGER)
        self.client.force_login(pm)
        response = self.client.get(reverse('pm_portal:dashboard'))
        self.assertContains(response, 'Активных проектов нет')


class CatalogTests(TestCase):
    def setUp(self):
        self.head = UserModel.objects.create_user(username='head-cat', password='x', role=User.Role.HEAD)

    def test_head_opens_catalog(self):
        self.client.force_login(self.head)
        response = self.client.get(reverse('ui:catalog'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for marker in ('gp-btn--primary', 'gp-badge--success', 'gp-card', 'gp-modal', 'gp-drawer', 'gp-dropdown',
                       'gp-tabs', 'gp-table', 'gp-pagination', 'gp-alert--danger', 'gp-skeleton', 'gp-empty',
                       'gp-loading', 'gp-breadcrumbs', 'gp-page-header', 'gp-input', 'gp-select', 'gp-textarea', 'gp-check'):
            self.assertIn(marker, html, marker)
        self.assertNotIn('gp2/legacy.css', html)

    def test_anonymous_goes_to_login(self):
        response = self.client.get(reverse('ui:catalog'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_other_roles_are_forbidden(self):
        intern = UserModel.objects.create_user(username='intern-cat', password='x', role=User.Role.INTERN)
        self.client.force_login(intern)
        self.assertEqual(self.client.get(reverse('ui:catalog')).status_code, 403)

    def test_errors_state_shows_field_errors(self):
        self.client.force_login(self.head)
        response = self.client.get(reverse('ui:catalog'), {'errors': '1'})
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'Название слишком короткое')

    def test_htmx_table_returns_only_fragment_sorted(self):
        self.client.force_login(self.head)
        response = self.client.get(
            reverse('ui:catalog'), {'sort': '-progress'},
            HTTP_HX_REQUEST='true', HTTP_HX_TARGET='demo-table',
        )
        html = response.content.decode()
        self.assertNotIn('gp-shell', html)
        self.assertEqual(html.count('<tr>'), 6)  # шапка + 5 строк страницы
        self.assertLess(html.index('100%'), html.index('91%'))

    def test_server_toast_header_is_ascii_json(self):
        self.client.force_login(self.head)
        response = self.client.post(reverse('ui:catalog_toast'), HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 204)
        header = response['HX-Trigger']
        header.encode('ascii')  # кириллица в заголовке сломала бы разбор в htmx
        self.assertEqual(json.loads(header)['gp:toast']['level'], 'success')


class AuthPagesTests(TestCase):
    def test_login_uses_new_layout(self):
        response = self.client.get(reverse('login'))
        html = response.content.decode()
        self.assertIn('gp-auth', html)
        self.assertIn('name="username"', html)
        self.assertNotIn('css/app.css', html)

    def test_500_renders_without_context(self):
        html = get_template('500.html').render({})
        self.assertIn('Что-то пошло не так', html)

    def test_404_page(self):
        response = self.client.get('/no-such-page-gp2/')
        self.assertEqual(response.status_code, 404)


class ComponentTests(TestCase):
    def test_button_escapes_label_and_filters_attributes(self):
        html = render('{% ui_button label variant="primary" hx_post="/x/" onclick="evil()" data_id=pk %}',
                      {'label': '<script>', 'pk': '"5"'})
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('hx-post="/x/"', html)
        self.assertIn('data-id="&quot;5&quot;"', html)
        self.assertNotIn('onclick', html)
        self.assertIn('gp-btn--primary', html)

    def test_button_as_link_and_icon_only(self):
        html = render('{% ui_button "Меню" href="/m/" icon_name="menu" label_hidden=True %}')
        self.assertTrue(html.startswith('<a href="/m/"'))
        self.assertIn('aria-label="Меню"', html)
        self.assertIn('gp-btn--icon', html)

    def test_alpine_attributes(self):
        html = render('{% ui_button "Открыть" x_on_click="open = true" x_bind_aria_expanded="open" %}')
        self.assertIn('x-on:click="open = true"', html)
        self.assertIn('x-bind:aria-expanded="open"', html)

    def test_badge_tone_is_normalized(self):
        self.assertIn('gp-badge--danger', render('{% ui_badge "Ошибка" tone="error" %}'))
        self.assertIn('gp-badge--neutral', render('{% ui_badge "x" tone="purple" %}'))

    def test_field_renders_errors_with_aria(self):
        class F(forms.Form):
            title = forms.CharField(label='Название', help_text='Подсказка')
            agree = forms.BooleanField(label='Согласен')
            kind = forms.ChoiceField(label='Тип', choices=[('a', 'A')])

        form = F({'title': ''})
        form.is_valid()
        html = render('{% ui_field form.title %}{% ui_field form.agree %}{% ui_field form.kind %}', {'form': form})
        self.assertIn('class="gp-input"', html)
        self.assertIn('aria-invalid="true"', html)
        self.assertIn('aria-describedby="id_title_help id_title_error"', html)
        self.assertIn('gp-check', html)
        self.assertIn('class="gp-select"', html)
        self.assertIn('gp-required', html)

    def test_pagination_keeps_filters(self):
        request = RequestFactory().get('/x/', {'status': 'active', 'page': '2'})
        page = Paginator(list(range(50)), 10).get_page(2)
        html = render('{% ui_pagination page %}', {'page': page}, request)
        self.assertIn('href="?status=active&amp;page=3"', html)
        self.assertIn('aria-current="page"', html)
        self.assertIn('11–20 из 50', html)

    def test_sort_toggles_and_resets_page(self):
        request = RequestFactory().get('/x/', {'sort': 'name', 'page': '3', 'q': 'а'})
        html = render('{% ui_sort "Название" "name" %}', request=request)
        self.assertIn('sort=-name', html)
        self.assertNotIn('page=3', html)
        self.assertIn('is-sorted', html)

    def test_card_slots(self):
        html = render('{% ui_card title=t %}Тело{% ui_slot "actions" %}<b>Действие</b>{% end_ui_slot %}'
                      '{% ui_slot "footer" %}Низ{% end_ui_slot %}{% end_ui_card %}', {'t': '<i>Карточка</i>'})
        self.assertIn('&lt;i&gt;Карточка&lt;/i&gt;', html)
        self.assertIn('<div class="gp-card__actions"><b>Действие</b></div>', html)
        self.assertIn('<footer class="gp-card__footer">Низ</footer>', html)
        self.assertIn('<div class="gp-card__body">Тело</div>', html)

    def test_datatable_labels_cells_for_mobile(self):
        request = RequestFactory().get('/x/')
        html = render('{% ui_datatable columns rows %}', {
            'columns': [{'key': 'name', 'label': 'Проект', 'primary': True}, {'key': 'stage', 'label': 'Этап'}],
            'rows': [{'name': 'Альфа', 'stage': {'badge': 'Сдан', 'tone': 'success'}}],
        }, request)
        self.assertIn('data-label="Этап"', html)
        self.assertIn('gp-table--stack', html)
        self.assertIn('gp-badge--success', html)

    def test_datatable_empty_state(self):
        html = render('{% ui_datatable columns rows empty_title="Пусто" %}', {'columns': [], 'rows': []}, RequestFactory().get('/'))
        self.assertIn('gp-empty', html)
        self.assertIn('Пусто', html)

    def test_modal_and_dropdown(self):
        html = render('{% ui_modal id="m1" title="Окно" %}Текст{% ui_slot "footer" %}OK{% end_ui_slot %}{% end_ui_modal %}'
                      '{% ui_dropdown label="Ещё" %}{% ui_menu_item "Удалить" danger=True %}{% end_ui_dropdown %}')
        self.assertIn('<dialog class="gp-modal" id="m1"', html)
        self.assertIn('aria-labelledby="m1-title"', html)
        self.assertIn('data-gp-close', html)
        self.assertIn('gp-menu__item--danger', html)
        self.assertIn('aria-haspopup="menu"', html)

    def test_states(self):
        self.assertEqual(render('{% ui_skeleton lines=2 %}').count('gp-skeleton--text'), 2)
        self.assertIn('htmx-indicator', render('{% ui_loading htmx=True id="l1" %}'))
        self.assertIn('gp-alert--warning', render('{% ui_alert "Внимание" level="warning" %}'))
        self.assertIn('href="/new/"', render('{% ui_empty "Пусто" action_label="Создать" action_href="/new/" %}'))
        self.assertIn('aria-current="page"', render('{% ui_breadcrumbs crumbs %}', {'crumbs': [('Главная', '/'), ('Здесь', '')]}))
        self.assertIn('is-active', render('{% ui_tabs tabs active="b" %}', {'tabs': [{'key': 'a', 'label': 'A', 'href': '?a'}, {'key': 'b', 'label': 'B', 'href': '?b'}]}))
        self.assertIn('>ТТ<', render('{% ui_avatar "Тест Тестов" %}'))


class TemplateHealthTests(TestCase):
    """Все шаблоны компилируются; внешние CDN не нужны для работы интерфейса."""

    def _template_names(self):
        from pathlib import Path

        from django.conf import settings

        root = Path(settings.BASE_DIR) / 'templates'
        names = [str(p.relative_to(root)) for p in root.rglob('*.html')]
        for app_dir in (Path(settings.BASE_DIR) / 'apps').glob('*/templates'):
            names += [str(p.relative_to(app_dir)) for p in app_dir.rglob('*.html')]
        return names

    def test_every_template_compiles(self):
        names = self._template_names()
        self.assertGreater(len(names), 150)
        for name in names:
            with self.subTest(template=name):
                get_template(name)

    def test_no_external_scripts_or_styles(self):
        import re
        from pathlib import Path

        from django.conf import settings

        external = re.compile(r'<(?:script|link)\b[^>]*(?:src|href)=["\'](?:https?:)?//', re.I)
        for path in (Path(settings.BASE_DIR) / 'templates').rglob('*.html'):
            with self.subTest(template=str(path)):
                self.assertIsNone(external.search(path.read_text()), 'внешний CDN в шаблоне')

    def test_new_layouts_label_every_input(self):
        """В новых шаблонах у каждого поля ввода есть подпись (label for / aria-label)."""
        import re

        head = UserModel.objects.create_user(username='a11y-head', password='x', role=User.Role.HEAD)
        self.client.force_login(head)
        for url in (reverse('ui:catalog'), reverse('ui:catalog') + '?errors=1'):
            html = self.client.get(url).content.decode()
            labelled = set(re.findall(r'<label[^>]+for="([^"]+)"', html))
            for match in re.finditer(r'<(input|select|textarea)\b([^>]*)>', html):
                attrs = match.group(2)
                if re.search(r'type="(hidden|submit|button)"', attrs) or 'csrfmiddlewaretoken' in attrs:
                    continue
                field_id = re.search(r'\bid="([^"]+)"', attrs)
                with self.subTest(url=url, field=attrs[:80]):
                    self.assertTrue(
                        (field_id and field_id.group(1) in labelled) or 'aria-label=' in attrs,
                        'поле без подписи',
                    )
        self.client.logout()
        html = self.client.get(reverse('login')).content.decode()
        self.assertIn('for="id_username"', html)
        self.assertIn('for="id_password"', html)

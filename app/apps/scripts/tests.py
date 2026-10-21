from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.scripts.models import Script


class ScriptTests(TestCase):
    """Скрипты: сохранить текст и потом его скопировать."""

    def setUp(self):
        self.user = User.objects.create_user(username='pm', password='pass12345')
        self.client.force_login(self.user)
        self.url = reverse('scripts:list')

    def test_login_required(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_create_keeps_text_as_is(self):
        body = 'Здравствуйте!\n\nПриглашаем вас на собеседование.\n— GeeksPro'
        self.client.post(reverse('scripts:create'), {
            'title': 'Приглашение', 'category': 'Кандидатам', 'body': body,
        })
        script = Script.objects.get(title='Приглашение')
        self.assertEqual(script.body, body)
        self.assertEqual(script.created_by, self.user)

    def test_text_is_shown_for_copying(self):
        Script.objects.create(title='Оффер', body='Текст оффера')
        response = self.client.get(self.url)
        self.assertContains(response, 'Текст оффера')
        self.assertContains(response, 'data-copy-script')

    def test_search_looks_inside_the_text(self):
        Script.objects.create(title='Первый', body='про стажировку')
        Script.objects.create(title='Второй', body='про оплату')
        titles = [
            s.title for s in self.client.get(self.url, {'q': 'оплату'}).context['scripts']
        ]
        self.assertEqual(titles, ['Второй'])

    def test_filter_by_category(self):
        Script.objects.create(title='А', body='x', category='Стажёрам')
        Script.objects.create(title='Б', body='x', category='Клиентам')
        titles = [
            s.title for s in
            self.client.get(self.url, {'category': 'Клиентам'}).context['scripts']
        ]
        self.assertEqual(titles, ['Б'])

    def test_pinned_scripts_go_first(self):
        Script.objects.create(title='Обычный', body='x')
        Script.objects.create(title='Важный', body='x', is_pinned=True)
        titles = [s.title for s in self.client.get(self.url).context['scripts']]
        self.assertEqual(titles[0], 'Важный')

    def test_pin_toggles(self):
        script = Script.objects.create(title='Текст', body='x')
        self.client.post(reverse('scripts:pin', args=[script.pk]))
        script.refresh_from_db()
        self.assertTrue(script.is_pinned)
        self.client.post(reverse('scripts:pin', args=[script.pk]))
        script.refresh_from_db()
        self.assertFalse(script.is_pinned)

    def test_update_changes_the_text(self):
        script = Script.objects.create(title='Старый', body='старый текст')
        self.client.post(reverse('scripts:update', args=[script.pk]), {
            'title': 'Новый', 'category': '', 'body': 'новый текст',
        })
        script.refresh_from_db()
        self.assertEqual(script.title, 'Новый')
        self.assertEqual(script.body, 'новый текст')

    def test_delete_needs_post(self):
        script = Script.objects.create(title='Удаляемый', body='x')
        self.client.get(reverse('scripts:delete', args=[script.pk]))
        self.assertTrue(Script.objects.filter(pk=script.pk).exists())
        self.client.post(reverse('scripts:delete', args=[script.pk]))
        self.assertFalse(Script.objects.filter(pk=script.pk).exists())

    def test_menu_has_the_section(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Скрипты')
        self.assertContains(response, reverse('scripts:list'))

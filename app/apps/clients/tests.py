"""Этап 11C: карточка проекта с заказчиком (на реальных данных падала)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.clients.models import Client
from apps.projects.models import Project

User = get_user_model()


class ClientLinkTests(TestCase):
    def test_absolute_url(self):
        client_obj = Client.objects.create(organization='ОсОО Заказчик')
        self.assertEqual(client_obj.get_absolute_url(), reverse('clients:detail', args=[client_obj.pk]))

    def test_head_project_card_with_client(self):
        client_obj = Client.objects.create(organization='ОсОО Заказчик')
        project = Project.objects.create(name='С заказчиком', client=client_obj, status='active')
        self.client.force_login(User.objects.create_user(username='head', password='x', role='head'))
        response = self.client.get(reverse('projects:detail', args=[project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('clients:detail', args=[client_obj.pk]))

"""Этап 11A: конфигурация инфраструктуры — в dev-контейнере (GP2_INFRA_DIR, только
чтение). Полная семантическая проверка — scripts/check-compose.sh на хосте."""
import os
import re
from pathlib import Path

from django.test import SimpleTestCase, TestCase

INFRA = os.environ.get('GP2_INFRA_DIR')


class InfraConfigTests(SimpleTestCase):
    def setUp(self):
        if not INFRA:
            self.skipTest('нет GP2_INFRA_DIR — проверка на хосте: scripts/check-compose.sh')
        self.root = Path(INFRA)

    def read(self, name):
        path = self.root / name
        self.assertTrue(path.exists(), f'{name} не смонтирован')
        return path.read_text()

    def test_prod_compose_isolation_and_single_poller(self):
        prod = '\n'.join(line.split(' #')[0] for line in self.read('docker-compose.prod.yml').splitlines()
                         if not line.lstrip().startswith('#'))  # без комментариев
        self.assertRegex(prod, r'(?m)^name: geekspro2$')
        self.assertNotIn('external: true', prod)
        self.assertNotIn('container_name', prod)
        self.assertEqual(len(re.findall(r'run_(telegram|graduate)_bot', prod)), 1)
        self.assertIn('profiles: ["bot"]', prod)
        for name in ('geekspro2_postgres_data', 'geekspro2_internal'):
            self.assertIn(name, prod)
        self.assertIn('127.0.0.1:${WEB_PORT:-8100}:8000', prod)
        self.assertNotIn('INSTALL_DEV', prod)  # prod-образ без dev-зависимостей
        self.assertIn('oom_score_adj: 500', prod)

    def test_dev_only_dev_dependencies(self):
        dev = self.read('docker-compose.yml')
        self.assertIn('INSTALL_DEV: "1"', dev)
        dockerfile = self.read('Dockerfile')
        self.assertIn('ARG INSTALL_DEV=0', dockerfile)
        self.assertIn('if [ "$INSTALL_DEV" = "1" ]; then pip install -r requirements-dev.txt', dockerfile)
        base = self.read('requirements.txt')
        for package in self.read('requirements-dev.txt').splitlines():
            name = package.split('==')[0].strip()
            if name and not name.startswith(('#', '-r')):
                self.assertNotIn(name, base, f'{name} попал в production-зависимости')

    def test_stage_template_has_no_secrets_and_outbound_off(self):
        env = dict(line.split('=', 1) for line in self.read('.env.prod.example').splitlines()
                   if '=' in line and not line.startswith('#'))
        self.assertEqual(env.get('GP2_ENVIRONMENT'), 'stage')
        self.assertEqual(env.get('TELEGRAM_OUTBOUND_MODE'), 'off')
        self.assertFalse(env.get('TELEGRAM_BOT_TOKEN'))
        joined = '\n'.join(env.values())
        self.assertIsNone(re.search(r'\d{8,10}:AA[\w-]{30,}', joined))
        self.assertNotIn('BEGIN', joined)


class ReadinessTests(TestCase):
    def test_readyz_reports_dependencies_without_details(self):
        response = self.client.get('/readyz/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'db': 'ok', 'cache': 'ok'})
        from unittest import mock

        with mock.patch('django.core.cache.cache.set', side_effect=RuntimeError('redis down')):
            failed = self.client.get('/readyz/')
        self.assertEqual((failed.status_code, failed.json()['cache']), (503, 'fail'))
        self.assertNotIn('redis down', failed.content.decode())  # без подробностей наружу

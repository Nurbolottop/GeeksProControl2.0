"""Этап 11C: в production видеособрания — только через настоящий JaaS."""
from django.test import SimpleTestCase, override_settings

from apps.video import services
from apps.video.providers import unavailable_reason

JAAS_KEYS = dict(JAAS_APP_ID='vpaas-magic-cookie-x', JAAS_KEY_ID='kid', JAAS_PRIVATE_KEY_PATH='/nonexistent.pem',
                 JAAS_WEBHOOK_SECRET='s')


class VideoAvailabilityTests(SimpleTestCase):
    @override_settings(GP2_ENVIRONMENT='stage', VIDEO_PROVIDER='simulator')
    def test_simulator_allowed_outside_production(self):
        self.assertEqual(unavailable_reason(), '')

    @override_settings(GP2_ENVIRONMENT='production', VIDEO_PROVIDER='simulator', VIDEO_SIMULATOR_SECRET='x')
    def test_simulator_refused_in_production(self):
        self.assertIn('JaaS не подключён', unavailable_reason())

    @override_settings(GP2_ENVIRONMENT='production', VIDEO_PROVIDER='jaas', JAAS_APP_ID='', JAAS_KEY_ID='',
                       JAAS_PRIVATE_KEY_PATH='', JAAS_WEBHOOK_SECRET='')
    def test_jaas_without_keys_refused(self):
        self.assertIn('не заданы ключи', unavailable_reason())

    @override_settings(GP2_ENVIRONMENT='production', VIDEO_PROVIDER='jaas', JAAS_ANONYMOUS_BLOCKED_VERIFIED=False,
                       **JAAS_KEYS)
    def test_jaas_without_release_gate_refused(self):
        self.assertIn('без JWT', unavailable_reason())

    @override_settings(GP2_ENVIRONMENT='production', VIDEO_PROVIDER='jaas', JAAS_ANONYMOUS_BLOCKED_VERIFIED=True,
                       **JAAS_KEYS)
    def test_jaas_fully_configured_allowed(self):
        self.assertEqual(unavailable_reason(), '')

    @override_settings(GP2_ENVIRONMENT='production', VIDEO_PROVIDER='simulator')
    def test_create_meeting_refused_before_any_write(self):
        with self.assertRaises(services.MeetingError):
            services.create_meeting(None, None, kind='direction', topic='x', starts_at=None)

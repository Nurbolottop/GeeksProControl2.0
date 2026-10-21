"""Видеопровайдеры (этап 9). get_provider() — по настройке VIDEO_PROVIDER:
'jaas' — Jitsi as a Service (8x8), 'simulator' — локальный симулятор."""
from django.conf import settings


def get_provider(name=None):
    from apps.video.providers.jaas import JaasProvider
    from apps.video.providers.simulator import SimulatorProvider

    name = name or getattr(settings, 'VIDEO_PROVIDER', 'simulator')
    return {'jaas': JaasProvider, 'simulator': SimulatorProvider}.get(name, SimulatorProvider)()

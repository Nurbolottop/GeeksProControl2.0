"""Видеопровайдеры (этап 9). get_provider() — по настройке VIDEO_PROVIDER:
'jaas' — Jitsi as a Service (8x8), 'simulator' — локальный симулятор."""
from django.conf import settings


def get_provider(name=None):
    from apps.video.providers.jaas import JaasProvider
    from apps.video.providers.simulator import SimulatorProvider

    name = name or getattr(settings, 'VIDEO_PROVIDER', 'simulator')
    return {'jaas': JaasProvider, 'simulator': SimulatorProvider}.get(name, SimulatorProvider)()


def unavailable_reason() -> str:
    """Почему видеособрания нельзя назначать ('' — можно). В production —
    только настоящий JaaS с ключами и подтверждённым запретом входа без JWT;
    симулятор там не допускается: его ссылки ведут в никуда."""
    if getattr(settings, 'GP2_ENVIRONMENT', '') != 'production':
        return ''
    provider = get_provider()
    if provider.name != 'jaas':
        return 'Видеособрания пока недоступны: видеосервис JaaS не подключён.'
    if not provider.configured():
        return 'Видеособрания пока недоступны: не заданы ключи JaaS (' + ', '.join(provider.missing_settings()) + ').'
    if provider.release_gate():
        return 'Видеособрания пока недоступны: не подтверждён запрет входа в JaaS без JWT.'
    return ''

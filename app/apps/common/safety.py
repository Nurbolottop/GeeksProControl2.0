"""Защита от случайного использования ресурсов действующей версии 1.0.

2.0 разрабатывается рядом с работающей 1.0 и начиналась как её копия —
вместе с .env, где лежал токен боевого бота и ID рабочей таблицы.
Скопировать такой .env ещё раз легко, поэтому сверяемся со списком
запрещённых значений: в настройках только их SHA-256
(PROTECTED_VALUE_HASHES), сами значения в репозиторий не попадают.
"""
import hashlib
import re

from django.conf import settings
from django.core.checks import Error, Warning, register


def sha256(value: str) -> str:
    return hashlib.sha256((value or '').strip().encode()).hexdigest()


def is_protected(value: str) -> bool:
    """Значение принадлежит 1.0 и в 2.0 использоваться не должно."""
    if not value:
        return False
    return sha256(value) in getattr(settings, 'PROTECTED_VALUE_HASHES', set())


# Что сверяем: настройка → как назвать её в ошибке.
GUARDED_SETTINGS = {
    'TELEGRAM_BOT_TOKEN': 'токен Telegram-бота 1.0',
    'RESERVE_SHEET_ID': 'ID рабочей Google-таблицы 1.0',
}


def protected_settings() -> list[str]:
    """Настройки 2.0, в которых сейчас стоит значение 1.0."""
    return [
        name for name in GUARDED_SETTINGS
        if is_protected(getattr(settings, name, ''))
    ]


@register()
def check_no_gp1_values(app_configs, **kwargs):
    """`manage.py check`, runserver и migrate падают, пока в .env значение 1.0."""
    return [
        Error(
            f'{name}: в настройках 2.0 стоит {GUARDED_SETTINGS[name]}.',
            hint='Задайте собственное значение 2.0 (тестовый бот, тестовая таблица).',
            id='gp2.E001',
        )
        for name in protected_settings()
    ]


@register()
def check_outbound_mode(app_configs, **kwargs):
    mode = settings.TELEGRAM_OUTBOUND_MODE
    environment = settings.GP2_ENVIRONMENT
    errors = []
    if mode not in ('off', 'allowlist', 'on'):
        errors.append(Error(
            f'TELEGRAM_OUTBOUND_MODE={mode!r}: допустимо off, allowlist или on.',
            id='gp2.E002',
        ))
    if environment not in ('development', 'stage', 'production'):
        errors.append(Error(
            f'GP2_ENVIRONMENT={environment!r}: допустимо development, stage или production.',
            id='gp2.E004',
        ))
    elif mode == 'on' and environment != 'production':
        # Рассылка всем — только после финальной миграции и разрешения
        # руководителя (docs/migration-plan.md, шаг «Запуск»).
        errors.append(Error(
            f'TELEGRAM_OUTBOUND_MODE=on запрещён при GP2_ENVIRONMENT={environment}.',
            hint='На stage используйте off или allowlist с тестовыми chat_id.',
            id='gp2.E003',
        ))
    return errors


TOKEN_RE = re.compile(r'^\d{5,15}:[A-Za-z0-9_-]{30,64}$')
USERNAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]{3,30}[Bb][Oo][Tt]$')


def token_format_ok(token: str) -> bool:
    """Формат токена Bot API (без обращения к Telegram)."""
    return bool(TOKEN_RE.match(token or ''))


@register()
def check_telegram_config(app_configs, **kwargs):
    """Конфигурация Telegram — только локально, без запросов к Telegram."""
    errors = []
    token = settings.TELEGRAM_BOT_TOKEN
    if token and not token_format_ok(token):
        errors.append(Error('TELEGRAM_BOT_TOKEN: неверный формат (ожидается 123456:ABC…).', id='gp2.E005'))
    username = settings.TELEGRAM_BOT_USERNAME
    if username and not USERNAME_RE.match(username):
        errors.append(Error(f'TELEGRAM_BOT_USERNAME={username!r}: username бота должен оканчиваться на «bot».', id='gp2.E006'))
    bad_users = [c for c in settings.TELEGRAM_TEST_CHAT_IDS if not c.isdigit()]
    if bad_users:
        errors.append(Error('TELEGRAM_TEST_CHAT_IDS: только положительные числовые id пользователей.', id='gp2.E007'))
    bad_groups = [c for c in settings.TELEGRAM_TEST_GROUP_IDS if not re.fullmatch(r'-\d+', c)]
    if bad_groups:
        errors.append(Error('TELEGRAM_TEST_GROUP_IDS: только отрицательные id групп (вида -100…).', id='gp2.E008'))
    if settings.TELEGRAM_OUTBOUND_MODE == 'allowlist' and not (
        settings.TELEGRAM_TEST_CHAT_IDS or settings.TELEGRAM_TEST_GROUP_IDS
    ):
        errors.append(Warning('TELEGRAM_OUTBOUND_MODE=allowlist, но списки тестовых чатов пусты — писать некому.', id='gp2.W001'))
    return errors


@register()
def check_video_config(app_configs, **kwargs):
    """Видеособрания (этап 10A): release-gate JaaS и хранение приватного ключа."""
    import os

    from django.core.checks import Warning as CheckWarning

    errors = []
    if getattr(settings, 'VIDEO_PROVIDER', 'simulator') != 'jaas':
        return errors
    if settings.GP2_ENVIRONMENT == 'production' and not getattr(settings, 'JAAS_ANONYMOUS_BLOCKED_VERIFIED', False):
        errors.append(CheckWarning(
            'JaaS в production без подтверждённого запрета входа без JWT — вход на собрания заблокирован.',
            hint='Проверьте в консоли JaaS, что анонимный вход запрещён, и задайте JAAS_ANONYMOUS_BLOCKED_VERIFIED=1.',
            id='gp2.W010'))
    path = getattr(settings, 'JAAS_PRIVATE_KEY_PATH', '')
    if path:
        real = os.path.realpath(path)
        if real.startswith(os.path.realpath(str(settings.BASE_DIR)) + os.sep):
            errors.append(Error('JAAS_PRIVATE_KEY_PATH внутри каталога проекта — ключ может попасть в Git.',
                                hint='Храните ключ вне репозитория (секреты сервера).', id='gp2.E010'))
        elif os.path.exists(real) and os.stat(real).st_mode & 0o077:
            errors.append(CheckWarning('Файл ключа JaaS доступен не только владельцу (нужно chmod 600).',
                                       id='gp2.W011'))
    return errors

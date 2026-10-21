"""Нижний рубеж защиты Telegram: каждый HTTP-запрос к Bot API.

pyTelegramBotAPI отправляет любой вызов (sendMessage, getUpdates,
setWebhook, createForumTopic…) через apihelper._make_request, а тот — через
apihelper.CUSTOM_REQUEST_SENDER, если он задан. install() ставит туда
guarded_sender: запрос либо уходит, либо падает с TelegramBlocked до сети.

Правила (вне production — строго по allowlist):
  * TELEGRAM_OUTBOUND_MODE=off — заблокировано ВСЁ, даже getMe и getUpdates;
  * mode=on вне production — заблокировано всё (массовые рассылки только в prod);
  * allowlist:
      - getMe / getUpdates / getWebhookInfo / deleteWebhook — разрешены
        (нужны polling'у, адресата у них нет);
      - setWebhook — запрещён вне production;
      - методы тем (createForumTopic…) — только в группах из
        TELEGRAM_TEST_GROUP_IDS (неизвестные группы — запрет);
      - остальное с адресатом — только чаты из TELEGRAM_TEST_CHAT_IDS или
        TELEGRAM_TEST_GROUP_IDS; без адресата — запрет.
  Реальные стажёры в allowlist не попадают, значит и сообщений не получат.

Узкие исключения (этап 8) — вызовы без chat_id, которые нужны, чтобы
ответить на уже принятое входящее обновление:
  * answerCallbackQuery — только для callback_query_id, который диспетчер
    принял (permit('callback', id)) из разрешённого чата;
  * getFile — только для file_id из принятого сообщения (permit('file', id));
  * скачивание файла (идёт мимо apihelper — проверяет GuardedTeleBot) —
    только путь, который вернул разрешённый getFile (permit('path', …)).
  Разрешения живут в памяти процесса, ограничены по размеру и не дают
  ничего отправить: только ответить на нажатие и прочитать присланный файл.
Темы в production — только в группах, привязанных к проекту в Control.

Токен в логи и сообщения об ошибках не попадает: метод берём из URL, сам
URL (в нём токен) нигде не печатаем.
"""
import logging
import time
from collections import OrderedDict

from django.conf import settings

logger = logging.getLogger(__name__)

SELF_METHODS = frozenset({'getMe', 'getUpdates', 'getWebhookInfo', 'deleteWebhook'})
WEBHOOK_METHODS = frozenset({'setWebhook'})
TOPIC_METHODS = frozenset({
    'createForumTopic', 'editForumTopic', 'closeForumTopic', 'reopenForumTopic',
    'deleteForumTopic', 'unpinAllForumTopicMessages', 'editGeneralForumTopic',
    'closeGeneralForumTopic', 'reopenGeneralForumTopic', 'hideGeneralForumTopic',
    'unhideGeneralForumTopic', 'unpinAllGeneralForumTopicMessages',
})


PERMIT_METHODS = {'answerCallbackQuery': ('callback', 'callback_query_id'), 'getFile': ('file', 'file_id')}
_PERMITS: dict[str, OrderedDict] = {'callback': OrderedDict(), 'file': OrderedDict(), 'path': OrderedDict()}
PERMIT_LIMIT = 500


class TelegramBlocked(Exception):
    """Запрос к Telegram остановлен защитой GeeksPro 2.0 (до отправки в сеть)."""


def permit(kind: str, value) -> None:
    """Разрешить один ответный вызов для значения из принятого обновления."""
    store = _PERMITS[kind]
    store[str(value)] = True
    while len(store) > PERMIT_LIMIT:
        store.popitem(last=False)


def permitted(kind: str, value) -> bool:
    return str(value) in _PERMITS[kind]


def clear_permits() -> None:
    for store in _PERMITS.values():
        store.clear()


def _bound_group(chat: str) -> bool:
    """Группа привязана к проекту или служебная — в Control (этап 8B)."""
    try:
        from apps.tgbot.models import TelegramGroup

        return TelegramGroup.objects.filter(chat_id=int(chat), status=TelegramGroup.Status.ACTIVE).exists()
    except Exception:  # любая ошибка проверки — запрет (fail closed), не пропуск
        return False


def check_download(file_path: str) -> None:
    """Скачивание файла: только путь из разрешённого getFile."""
    mode = settings.TELEGRAM_OUTBOUND_MODE
    production = settings.GP2_ENVIRONMENT == 'production'
    if mode == 'off' or (mode == 'on' and not production):
        raise TelegramBlocked(f'downloadFile: режим {mode} — скачивание запрещено.')
    if not permitted('path', file_path):
        raise TelegramBlocked('downloadFile: файл не из принятого сообщения.')


def _method_name(url: str) -> str:
    return url.rstrip('/').rsplit('/', 1)[-1]


def _chat_id(params) -> str:
    if not params:
        return ''
    return str(params.get('chat_id', '')).strip()


def check(method: str, params=None) -> None:
    """Разрешён ли вызов method с такими параметрами. Иначе — TelegramBlocked."""
    mode = settings.TELEGRAM_OUTBOUND_MODE
    production = settings.GP2_ENVIRONMENT == 'production'
    if mode == 'off':
        raise TelegramBlocked(f'{method}: TELEGRAM_OUTBOUND_MODE=off — запросы к Telegram запрещены.')
    if mode == 'on' and not production:
        raise TelegramBlocked(f'{method}: режим on вне production запрещён (массовые рассылки — только в prod).')
    if mode == 'on' and production and method not in TOPIC_METHODS:
        return
    if mode == 'on' and production:
        if not _bound_group(_chat_id(params)):
            raise TelegramBlocked(f'{method}: группа не привязана к проекту в Control — темы не создаём.')
        return

    chat = _chat_id(params)
    users = settings.TELEGRAM_TEST_CHAT_IDS
    groups = settings.TELEGRAM_TEST_GROUP_IDS

    if method in SELF_METHODS:
        return
    if method in PERMIT_METHODS:
        kind, field = PERMIT_METHODS[method]
        if not permitted(kind, (params or {}).get(field, '')):
            raise TelegramBlocked(f'{method}: не из принятого входящего обновления.')
        return
    if method in WEBHOOK_METHODS and not production:
        raise TelegramBlocked(f'{method}: webhook вне production не включается.')
    if method in TOPIC_METHODS:
        # TODO(группы проектов): в production — только подтверждённые в Control группы
        if chat not in groups:
            raise TelegramBlocked(f'{method}: группа не в TELEGRAM_TEST_GROUP_IDS — темы не создаём.')
        return
    if not chat:
        raise TelegramBlocked(f'{method}: вызов без адресата вне production запрещён.')
    if chat not in users and chat not in groups:
        raise TelegramBlocked(f'{method}: чат не в allowlist тестовых пользователей и групп.')


def guarded_sender(method, url, **kwargs):
    """Замена отправителя apihelper: проверка, затем обычный HTTP-запрос."""
    from requests.exceptions import ConnectionError as RequestsConnectionError
    from telebot import apihelper

    name = _method_name(url)
    try:
        check(name, kwargs.get('params'))
    except TelegramBlocked as exc:
        logger.info('Telegram заблокирован: %s', exc)
        raise
    # Повторы, как в штатном пути apihelper (с CUSTOM_REQUEST_SENDER он их не делает)
    attempts = max(1, apihelper.MAX_RETRIES if apihelper.RETRY_ON_ERROR else 1)
    for attempt in range(1, attempts + 1):
        try:
            return apihelper._get_req_session().request(method, url, **kwargs)
        except RequestsConnectionError:
            if attempt == attempts:
                raise
            time.sleep(apihelper.RETRY_TIMEOUT)


def install() -> None:
    """Подключить защиту ко всем запросам pyTelegramBotAPI в этом процессе."""
    from telebot import apihelper

    apihelper.CUSTOM_REQUEST_SENDER = guarded_sender


def is_installed() -> bool:
    from telebot import apihelper

    return apihelper.CUSTOM_REQUEST_SENDER is guarded_sender

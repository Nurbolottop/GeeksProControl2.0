import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

# telebot.infinity_polling() при сбое сама уходит в повтор всего через 3
# секунды без остановки — на нагруженном хосте с потерей пакетов это
# вылилось в тысячи попыток за сутки с одного и того же IP подряд, и это
# подозрительно похоже на то, что сеть/Telegram начали этот IP душить как
# спамера. Поэтому здесь — свой цикл поверх обычного polling(non_stop=False)
# с растущей паузой: 10с, 20с, … максимум 2 минуты между попытками.
RETRY_START_SECONDS = 10
RETRY_MAX_SECONDS = 120


def _hide_token(message: str) -> str:
    """requests/urllib3 кладут токен прямо в текст ошибки (URL запроса) —
    прячем его перед тем, как это попадёт в логи контейнера."""
    token = settings.TELEGRAM_BOT_TOKEN
    if token and token in message:
        secret = token.split(':', 1)[-1]
        message = message.replace(secret, '*' * len(secret))
    return message


class Command(BaseCommand):
    help = ('УСТАРЕЛО (этап 10A): основной бот — run_telegram_bot. Старый бот-выпускник '
            'запускается только явно с --legacy-standalone и никогда вместе с основным.')

    def add_arguments(self, parser):
        parser.add_argument('--legacy-standalone', action='store_true',
                            help='Осознанный запуск старого бота (только если run_telegram_bot не работает).')

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError(
                'TELEGRAM_BOT_TOKEN не задан в переменных окружения — бот не запущен.',
            )
        from apps.common.safety import is_protected

        if is_protected(settings.TELEGRAM_BOT_TOKEN):
            raise CommandError(
                'TELEGRAM_BOT_TOKEN принадлежит боту 1.0 — в 2.0 запускать с ним нельзя.',
            )
        if settings.TELEGRAM_OUTBOUND_MODE == 'off':
            # polling — это тоже запросы к Telegram; в off они все запрещены
            raise CommandError(
                'TELEGRAM_OUTBOUND_MODE=off — бот не запускается. Для теста: '
                'TELEGRAM_OUTBOUND_MODE=allowlist и свои id в TELEGRAM_TEST_CHAT_IDS.',
            )
        if not options.get('legacy_standalone'):
            raise CommandError('Основной бот 2.0 — run_telegram_bot (сценарий выпускника перенесён в apps.tgbot). '
                               'Старый бот запускается только с --legacy-standalone.')
        from apps.common.telegram_lock import PollingLocked, acquire_polling_lock

        try:
            acquire_polling_lock()
        except PollingLocked as error:
            raise CommandError(str(error)) from None
        from apps.graduate_bot.bot import bot

        self.stdout.write(f'Исходящие сообщения: TELEGRAM_OUTBOUND_MODE={settings.TELEGRAM_OUTBOUND_MODE}')

        self.stdout.write(self.style.SUCCESS('Бот-выпускник запущен, слушаю Telegram…'))
        delay = RETRY_START_SECONDS
        while True:
            try:
                # timeout — наш бюджет на запрос (столько ждём ответа),
                # long_polling_timeout — сколько Telegram держит соединение
                # открытым. Второе должно быть заметно меньше первого:
                # через медленный прокси дорога туда-обратно съедает
                # 5–15 секунд, и при равных значениях каждый опрос падал
                # по ReadTimeout, а бот выглядел молчащим.
                bot.polling(
                    non_stop=False, skip_pending=True,
                    timeout=45, long_polling_timeout=10,
                )
            except Exception as exc:  # noqa: BLE001 — бот должен пережить любой сбой связи
                message = _hide_token(str(exc))
                self.stderr.write(self.style.WARNING(
                    f'Бот-выпускник: сбой связи ({type(exc).__name__}: {message}), '
                    f'новая попытка через {delay} сек…',
                ))
                time.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_SECONDS)
            else:
                # polling() вышел сам (например, вызвали bot.stop_polling())
                # — это штатная остановка, не ошибка, пауза не нужна.
                delay = RETRY_START_SECONDS

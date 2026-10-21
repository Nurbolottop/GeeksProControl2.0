"""Диалог бота-выпускника в Telegram.

Вся бизнес-логика — в services.py (обычные, тестируемые функции), тут
только формулировки сообщений и разбор ответов. Состояние разговора —
только в памяти процесса (кто уже назвал ФИО, сколько раз ошибся с
телефоном): бот упал/перезапустился — человек просто начинает заново
командой /start, ничего в БД для этого не хранится.

Кнопки — обычная reply-клавиатура (types.ReplyKeyboardMarkup), не
инлайн: нажатие присылает текст кнопки обычным сообщением, а не
callback_query, поэтому весь выбор разбирается через
register_next_step_handler (как ФИО/телефон), а не через
callback_query_handler.
"""
import logging

import telebot
import urllib3.util.connection as urllib3_connection
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from telebot import apihelper, types

from apps.graduate_bot import services

logger = logging.getLogger(__name__)

# С этого сервера до конкретного IP api.telegram.org (149.154.166.110)
# стабильно нет маршрута — похоже на проблему пиринга дата-центра, не
# лечится ни ретраями, ни пересозданием контейнера. У другого проекта на
# этом же сервере та же проблема уже решена локальным SOCKS5-релеем на
# хосте (порт 40001) — используем его же. Без TELEGRAM_PROXY_URL бот
# просто ходит напрямую (для окружений, где прямой связи достаточно).
if settings.TELEGRAM_PROXY_URL:
    apihelper.proxy = {
        'http': settings.TELEGRAM_PROXY_URL,
        'https': settings.TELEGRAM_PROXY_URL,
    }
    # Через прокси новое соединение — это TCP + SOCKS5-рукопожатие + DNS
    # (socks5h резолвит на стороне прокси) + TLS заново, на каждый вызов:
    # заметная задержка (3–5 сек на каждое сообщение) при пересоздании
    # сессии на каждый запрос. Держим сессию 5 минут — соединение
    # переиспользуется, а протухшее всё равно не живёт долго.
    apihelper.SESSION_TIME_TO_LIVE = 300
else:
    # Без прокси, напрямую — здесь ровно наоборот: если сеть на секунду
    # оборвётся, соединение в вечном пуле останется битым, и лучше
    # каждый раз новое, чем застрять на мёртвом.
    apihelper.SESSION_TIME_TO_LIVE = 0

# api.telegram.org отдаёт и IPv4, и IPv6 адрес, а у контейнера рабочего
# IPv6-маршрута нет — часть попыток соединения выбирает IPv6 и падает
# мгновенно (ENETUNREACH), вместо того чтобы попробовать IPv4. Отключаем
# IPv6 на уровне urllib3, а не чиним сеть контейнера — так же safer и не
# трогает остальные проекты на этом сервере.
urllib3_connection.HAS_IPV6 = False

# До Telegram с этого сервера идём через прокси, и он медленный: обычный
# ответ приходит за 5–15 секунд, иногда дольше. Стандартных таймаутов
# библиотеки (15/30 сек) на это не хватает — сообщение выпускнику просто
# не уходит. Даём запас и разрешаем повтор: лучше ответить с задержкой,
# чем не ответить совсем.
apihelper.CONNECT_TIMEOUT = 30
apihelper.READ_TIMEOUT = 60
apihelper.RETRY_ON_ERROR = True
apihelper.RETRY_TIMEOUT = 3
apihelper.MAX_RETRIES = 3

from apps.common.outbound import GuardedTeleBot  # noqa: E402
from apps.common.safety import is_protected  # noqa: E402

if is_protected(settings.TELEGRAM_BOT_TOKEN):
    # Токен действующего бота 1.0: второй polling отберёт у него обновления,
    # а сообщения уйдут реальным людям.
    raise ImproperlyConfigured('TELEGRAM_BOT_TOKEN принадлежит боту 1.0 — в 2.0 он запрещён.')

# Пишет только тем, кому разрешено TELEGRAM_OUTBOUND_MODE (apps.common.outbound).
# Формат токена не проверяем при импорте: без токена (разработка, тесты)
# модуль должен импортироваться, а запуск без токена и так запрещает
# run_graduate_bot.
bot = GuardedTeleBot(settings.TELEGRAM_BOT_TOKEN, validate_token=False)

RULES_BUTTON = 'Ознакомиться с правилами'
RULES_OK_BUTTON = 'Я ознакомлен'
LOGIN_BUTTON = 'Войти в аккаунт'
CONTINUE_BUTTON = 'Продолжить стажировку'
FINISH_BUTTON = 'Закончить стажировку'
BANK_AGREE_BUTTON = 'Я согласен'
BANK_SENT_BUTTON = 'Отправил заявку'
PLAY_BUTTON = 'Играть'
SHARE_PHONE_BUTTON = '📱 Отправить мой номер'

RULES_TEXT = (
    '📋 О боте и правилах\n\n'
    'Этот бот — для выпускников стажировки GeeksPro. Через него вы '
    'решаете, что делать дальше: продолжить стажировку на новом проекте '
    'или закончить её и попасть в наш банк резюме.\n\n'
    'Как это работает:\n'
    '1. Вы входите в аккаунт — называете ФИО и номер телефона, которые '
    'указывали в анкете стажёра. Так мы убеждаемся, что это действительно вы.\n'
    '2. Выбираете одно из двух: продолжить или закончить стажировку.\n'
    '3. Дальше бот подсказывает, что делать, и присылает новости по вашему выбору.\n\n'
    'Важно:\n'
    '— Выбор делается один раз и изменить его в боте нельзя. Если '
    'ошиблись — напишите руководителю GeeksPro.\n'
    '— Один аккаунт на один Telegram: войти за другого человека не получится.\n'
    '— Номер телефона нужен только для проверки личности, никуда больше он не уходит.'
)

BANK_ABOUT_TEXT = (
    '💼 Банк резюме GeeksPro\n\n'
    'Мы размещаем ваше резюме, портфолио и сертификат на нашем сайте: '
    'https://geeks.kg/direction\n\n'
    'Оттуда вы можете делиться своей страницей, а работодатели — '
    'смотреть кандидатов и выходить на вас напрямую.\n\n'
    'Если согласны, что мы публикуем ваши данные, нажмите кнопку ниже.'
)

BANK_HOWTO_TEXT = (
    'Что нужно сделать:\n\n'
    '1. Перейдите на https://geeks.kg/sign-in и зарегистрируйтесь.\n'
    '2. Заполните все данные о себе: направление, опыт, проекты, '
    'портфолио, сертификат.\n'
    '3. Отправьте заявку на сайте.\n'
    '4. Вернитесь сюда и нажмите «Отправил заявку».'
)

# Камень-ножницы-бумага: что кого бьёт и счёт по чатам. Счёт живёт до
# перезапуска бота — это игра на перерыв, хранить её в базе незачем.
GAME_MOVES = {'✊ Камень': 'rock', '✌️ Ножницы': 'scissors', '✋ Бумага': 'paper'}
GAME_BEATS = {'rock': 'scissors', 'scissors': 'paper', 'paper': 'rock'}
GAME_LABELS = {'rock': '✊ Камень', 'scissors': '✌️ Ножницы', 'paper': '✋ Бумага'}
_game_score: dict[int, dict] = {}


def _one_button(label):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton(label))
    return markup


def _choice_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton(CONTINUE_BUTTON))
    markup.add(types.KeyboardButton(FINISH_BUTTON))
    return markup


def _play_markup():
    return _one_button(PLAY_BUTTON)


def _game_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(*[types.KeyboardButton(name) for name in GAME_MOVES])
    return markup


@bot.message_handler(commands=['start'])
def handle_start(message):
    """Начало: правила → вход в аккаунт. Тем, кто уже всё выбрал,
    показываем их текущее состояние — выбор менять нельзя."""
    from apps.interns.models import GraduateStatus

    chat_id = message.chat.id
    intern = services.find_by_chat_id(chat_id)

    if intern is not None and intern.graduate_status == GraduateStatus.WAITING:
        # Ждёт распределения: кнопок выбора больше нет, только игра.
        bot.send_message(
            chat_id, services.resolved_status_message(intern),
            reply_markup=_play_markup(),
        )
        return
    if intern is not None and not intern.graduate_status:
        # Выбор уже сделан (банк резюме или проект) — просто статус.
        bot.send_message(
            chat_id, services.resolved_status_message(intern),
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return

    if not services.rules_accepted(chat_id, intern):
        bot.send_message(
            chat_id,
            'Здравствуйте! Это бот для выпускников стажировки GeeksPro.\n\n'
            'Прежде чем начать, прочитайте короткие правила.',
            reply_markup=_one_button(RULES_BUTTON),
        )
        bot.register_next_step_handler(message, handle_rules_request)
        return
    send_login_offer(message, intern)


def handle_rules_request(message):
    if (message.text or '').strip() != RULES_BUTTON:
        bot.send_message(
            message.chat.id, 'Пожалуйста, воспользуйтесь кнопкой ниже.',
            reply_markup=_one_button(RULES_BUTTON),
        )
        bot.register_next_step_handler(message, handle_rules_request)
        return
    bot.send_message(message.chat.id, RULES_TEXT, reply_markup=_one_button(RULES_OK_BUTTON))
    bot.register_next_step_handler(message, handle_rules_accept)


def handle_rules_accept(message):
    if (message.text or '').strip() != RULES_OK_BUTTON:
        bot.send_message(
            message.chat.id, 'Пожалуйста, нажмите «Я ознакомлен».',
            reply_markup=_one_button(RULES_OK_BUTTON),
        )
        bot.register_next_step_handler(message, handle_rules_accept)
        return
    services.accept_rules(message.chat.id)
    bot.send_message(
        message.chat.id,
        '🎉 Поздравляем с завершением стажировки в GeeksPro!\n\n'
        'Вы прошли настоящие проекты в команде — это уже опыт, который '
        'ценят работодатели.\n\nТеперь войдите в аккаунт, чтобы продолжить.',
        reply_markup=_one_button(LOGIN_BUTTON),
    )
    bot.register_next_step_handler(message, handle_login_request)


def send_login_offer(message, intern=None):
    bot.send_message(
        message.chat.id, 'Войдите в аккаунт, чтобы продолжить.',
        reply_markup=_one_button(LOGIN_BUTTON),
    )
    bot.register_next_step_handler(message, handle_login_request)


def handle_login_request(message):
    if (message.text or '').strip() != LOGIN_BUTTON:
        bot.send_message(
            message.chat.id, 'Пожалуйста, нажмите «Войти в аккаунт».',
            reply_markup=_one_button(LOGIN_BUTTON),
        )
        bot.register_next_step_handler(message, handle_login_request)
        return
    bot.send_message(
        message.chat.id,
        'Как вас зовут? Напишите фамилию и имя — можно в любом порядке, '
        'можно только имя, я поищу по базе.',
        reply_markup=types.ReplyKeyboardRemove(),
    )
    bot.register_next_step_handler(message, handle_name)


def handle_name(message):
    typed = (message.text or '').strip()
    candidates = services.find_graduates(typed)
    if not candidates:
        # Записываем, что именно ввели: иначе «бот меня не находит»
        # нечем проверить — в логах видно только молчание.
        others = services.find_people(typed)
        logger.warning(
            'Бот-выпускник: не нашли выпускника по «%s» (chat=%s); '
            'среди всех стажёров похожие: %s',
            typed, message.chat.id, [p.full_name for p in others] or 'нет',
        )
        if others:
            names = '\n'.join(f'— {p.full_name}' for p in others)
            bot.send_message(
                message.chat.id,
                f'Нашёл вас в базе:\n{names}\n\n'
                'Но бот открывается только после того, как ваш проект сдан '
                'и вас отметили выпускником. Если проект уже закончился — '
                'напишите руководителю GeeksPro, он отметит, и возвращайтесь: /start.',
            )
            return
        bot.send_message(
            message.chat.id,
            'Не нашёл такого человека. Попробуйте написать иначе — '
            'например, только фамилию. Если не получится, напишите '
            'руководителю GeeksPro.',
        )
        bot.register_next_step_handler(message, handle_name)
        return
    logger.info(
        'Бот-выпускник: по «%s» нашли %s', typed, [p.full_name for p in candidates],
    )
    if len(candidates) > 1:
        # Просить «введите полностью, как в анкете» бесполезно: человек не
        # знает, как он записан у нас. Показываем кнопками — пусть выберет.
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        for candidate in candidates:
            markup.add(types.KeyboardButton(candidate.full_name))
        bot.send_message(
            message.chat.id, 'Нашёл несколько человек. Выберите себя:',
            reply_markup=markup,
        )
        bot.register_next_step_handler(
            message, handle_name_choice, ids=[c.pk for c in candidates],
        )
        return
    ask_phone(message, candidates[0])


def handle_name_choice(message, ids):
    from apps.interns.models import Intern

    chosen = (message.text or '').strip()
    intern = Intern.objects.filter(pk__in=ids, full_name=chosen).first()
    if intern is None:
        bot.send_message(message.chat.id, 'Выберите себя кнопкой из списка ниже.')
        bot.register_next_step_handler(message, handle_name_choice, ids=ids)
        return
    ask_phone(message, intern)


def _share_phone_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton(SHARE_PHONE_BUTTON, request_contact=True))
    return markup


def _own_contact_phone(message):
    """Номер из кнопки «Отправить мой номер» — только свой собственный.

    Telegram разрешает переслать и чужой контакт, поэтому сверяем, что
    контакт принадлежит тому, кто пишет.
    """
    contact = getattr(message, 'contact', None)
    if contact is None:
        return None
    if contact.user_id and contact.user_id != message.from_user.id:
        return None
    return contact.phone_number


def ask_phone(message, intern):
    if not intern.phone:
        # Телефона в базе нет — сверять не с чем. Просим поделиться
        # контактом: Telegram отдаёт номер сам, в кнопке его не подделать.
        bot.send_message(
            message.chat.id,
            f'{intern.full_name}, вашего номера у нас в базе нет — '
            'подтвердите личность кнопкой ниже. Telegram передаст номер '
            'сам, вводить его руками не нужно.',
            reply_markup=_share_phone_markup(),
        )
        bot.register_next_step_handler(
            message, handle_shared_contact, intern_id=intern.pk,
        )
        return
    if services.is_phone_locked(intern):
        minutes = services.phone_lock_minutes_left(intern)
        bot.send_message(
            message.chat.id,
            f'Слишком много неверных попыток подряд. Попробуйте через '
            f'{minutes} мин или обратитесь к руководителю GeeksPro.',
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return
    bot.send_message(
        message.chat.id,
        f'{intern.full_name}, подтвердите личность: нажмите кнопку ниже '
        'или введите номер телефона из анкеты вручную. Формат любой.',
        reply_markup=_share_phone_markup(),
    )
    bot.register_next_step_handler(message, handle_phone, intern_id=intern.pk, attempt=1)


def handle_shared_contact(message, intern_id):
    """Человек поделился своим номером — записываем его и пускаем дальше."""
    from apps.interns.models import Intern

    intern = Intern.objects.filter(pk=intern_id).first()
    if intern is None:
        bot.send_message(message.chat.id, 'Что-то пошло не так — начните заново: /start.')
        return
    phone = _own_contact_phone(message)
    if not phone:
        bot.send_message(
            message.chat.id,
            'Нажмите кнопку ниже — так Telegram передаст именно ваш номер. '
            'Чужой контакт не подойдёт.',
            reply_markup=_share_phone_markup(),
        )
        bot.register_next_step_handler(
            message, handle_shared_contact, intern_id=intern_id,
        )
        return
    taken = services.chat_taken_by_other(intern, message.chat.id)
    if taken is not None:
        bot.send_message(
            message.chat.id,
            'В этом Telegram уже входил другой выпускник — '
            f'{taken.full_name}. Один аккаунт на один Telegram.',
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return
    services.save_phone_from_telegram(intern, phone, message.chat.id)
    show_choice(message, intern)


def handle_phone(message, intern_id, attempt):
    from apps.interns.models import Intern

    intern = Intern.objects.filter(pk=intern_id).first()
    if intern is None:
        bot.send_message(message.chat.id, 'Что-то пошло не так — начните заново: /start.')
        return
    shared = _own_contact_phone(message)
    entered = shared or (message.text or '')
    if not services.phone_matches(intern, entered):
        if attempt >= services.PHONE_ATTEMPTS_LIMIT:
            services.lock_phone_verification(intern)
            bot.send_message(
                message.chat.id,
                'Номер не подошёл несколько раз подряд. Проверка временно '
                f'заблокирована на {services.PHONE_LOCK_MINUTES} мин. Обратитесь к '
                'руководителю GeeksPro, если это ошибка.',
            )
            return
        left = services.PHONE_ATTEMPTS_LIMIT - attempt
        bot.send_message(
            message.chat.id,
            f'Номер не совпадает с тем, что в анкете. Попробуйте ещё раз '
            f'(осталось попыток: {left}).',
        )
        bot.register_next_step_handler(
            message, handle_phone, intern_id=intern_id, attempt=attempt + 1,
        )
        return
    taken = services.chat_taken_by_other(intern, message.chat.id)
    if taken is not None:
        bot.send_message(
            message.chat.id,
            'В этом Telegram уже входил другой выпускник — '
            f'{taken.full_name}. Один аккаунт на один Telegram: войдите со '
            'своего или обратитесь к руководителю GeeksPro.',
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return
    services.remember_chat_id(intern, message.chat.id)
    show_choice(message, intern)


def show_choice(message, intern):
    projects = services.completed_projects(intern)
    if projects:
        lines = '\n'.join(f'— {m.project.name}' for m in projects)
        projects_line = f'\n\nВы завершили проекты:\n{lines}'
    else:
        projects_line = ''

    bot.send_message(
        message.chat.id,
        f'Здравствуйте, {intern.full_name}!{projects_line}\n\n'
        'Что дальше? Выбор делается один раз, изменить его в боте нельзя.',
        reply_markup=_choice_markup(),
    )
    bot.register_next_step_handler(message, handle_choice, intern_id=intern.pk)


def handle_choice(message, intern_id):
    from apps.interns.models import Intern

    intern = Intern.objects.filter(pk=intern_id).first()
    if intern is None:
        bot.send_message(
            message.chat.id, 'Что-то пошло не так — начните заново: /start.',
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return
    choice = (message.text or '').strip()
    if choice == FINISH_BUTTON:
        send_bank_about(message, intern)
    elif choice == CONTINUE_BUTTON:
        join_waiting_list(message, intern)
    else:
        bot.send_message(
            message.chat.id, 'Пожалуйста, воспользуйтесь кнопками ниже.',
            reply_markup=_choice_markup(),
        )
        bot.register_next_step_handler(message, handle_choice, intern_id=intern_id)


def join_waiting_list(message, intern):
    """«Продолжить стажировку» — человек попадает в базу ожидания, а
    распределяет его руководитель со страницы «Ожидают проект»."""
    services.request_next_internship(intern, message.chat.id)
    bot.send_message(
        message.chat.id,
        f'Готово, {intern.full_name}! Вы в базе ожидания следующего '
        'проекта.\n\nКак только появится свободное место, вас добавят в '
        'команду и я напишу сюда — ничего делать не нужно.\n\n'
        'А пока можем сыграть, чтобы не скучать 🙂',
        reply_markup=_play_markup(),
    )


def send_bank_about(message, intern):
    if intern.resume_bank_status:
        # Уже подавал заявку раньше — просто говорим текущий статус.
        bot.send_message(
            message.chat.id, services.resolved_status_message(intern),
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return
    bot.send_message(
        message.chat.id, BANK_ABOUT_TEXT,
        reply_markup=_one_button(BANK_AGREE_BUTTON),
    )
    bot.register_next_step_handler(message, handle_bank_agree, intern_id=intern.pk)


def handle_bank_agree(message, intern_id):
    from apps.interns.models import Intern

    intern = Intern.objects.filter(pk=intern_id).first()
    if intern is None:
        bot.send_message(message.chat.id, 'Что-то пошло не так — начните заново: /start.')
        return
    if (message.text or '').strip() != BANK_AGREE_BUTTON:
        bot.send_message(message.chat.id, 'Пожалуйста, нажмите «Я согласен».')
        send_bank_about(message, intern)
        return
    bot.send_message(
        message.chat.id, BANK_HOWTO_TEXT,
        reply_markup=_one_button(BANK_SENT_BUTTON),
    )
    bot.register_next_step_handler(message, handle_bank_sent, intern_id=intern.pk)


def handle_bank_sent(message, intern_id):
    from apps.interns.models import Intern

    intern = Intern.objects.filter(pk=intern_id).first()
    if intern is None:
        bot.send_message(message.chat.id, 'Что-то пошло не так — начните заново: /start.')
        return
    if (message.text or '').strip() != BANK_SENT_BUTTON:
        bot.send_message(message.chat.id, 'Пожалуйста, нажмите «Отправил заявку».')
        bot.register_next_step_handler(message, handle_bank_sent, intern_id=intern_id)
        return
    submitted = services.submit_to_resume_bank(intern, message.chat.id)
    if submitted:
        text = (
            f'Спасибо, что прошли стажировку в GeeksPro, {intern.full_name}!\n\n'
            'Ваша заявка ушла на проверку руководителю. Когда данные '
            'проверят и опубликуют, я обязательно напишу сюда. Если '
            'что-то нужно будет поправить — тоже сообщу.'
        )
    else:
        text = 'Ваше резюме уже приняли ранее — менять ничего не нужно.'
    bot.send_message(message.chat.id, text, reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda message: (message.text or '').strip() == PLAY_BUTTON)
def handle_play(message):
    _game_score.pop(message.chat.id, None)
    bot.send_message(
        message.chat.id,
        '✊✌️✋ Камень-ножницы-бумага!\n\nВыбирайте — играем до тех пор, '
        'пока не надоест. Чтобы выйти, наберите /start.',
        reply_markup=_game_markup(),
    )


@bot.message_handler(func=lambda message: (message.text or '').strip() in GAME_MOVES)
def handle_game_move(message):
    import random

    player = GAME_MOVES[(message.text or '').strip()]
    computer = random.choice(list(GAME_BEATS))
    score = _game_score.setdefault(message.chat.id, {'you': 0, 'bot': 0, 'draw': 0})
    if player == computer:
        score['draw'] += 1
        result = 'Ничья!'
    elif GAME_BEATS[player] == computer:
        score['you'] += 1
        result = 'Вы выиграли! 🎉'
    else:
        score['bot'] += 1
        result = 'Выиграл бот 🤖'
    bot.send_message(
        message.chat.id,
        f'Вы: {GAME_LABELS[player]}\nБот: {GAME_LABELS[computer]}\n\n{result}\n\n'
        f'Счёт — вы {score["you"]} : {score["bot"]} бот (ничьих: {score["draw"]})',
        reply_markup=_game_markup(),
    )


# ------------------------------------------------ заморозка и возвращение (UI-03C)
# Заявку подаёт сам стажёр, решает руководитель (apps.interns.freeze).

CANCEL_BUTTON = 'Отмена'


def _known_intern(message):
    intern = services.find_by_chat_id(message.chat.id)
    if intern is None:
        bot.send_message(message.chat.id, 'Сначала войдите в аккаунт: /start.')
    return intern


@bot.message_handler(commands=['freeze'])
def handle_freeze(message):
    intern = _known_intern(message)
    if intern is None:
        return
    bot.send_message(
        message.chat.id,
        'Заявка на заморозку стажировки. Напишите причину одним сообщением — её увидит руководитель.\n\n'
        'После одобрения вы выйдете из всех текущих проектов.',
        reply_markup=_one_button(CANCEL_BUTTON),
    )
    bot.register_next_step_handler(message, handle_request_reason, intern_id=intern.pk, kind='freeze')


@bot.message_handler(commands=['return'])
def handle_return(message):
    intern = _known_intern(message)
    if intern is None:
        return
    bot.send_message(
        message.chat.id,
        'Заявка на возвращение из заморозки. Напишите причину одним сообщением.',
        reply_markup=_one_button(CANCEL_BUTTON),
    )
    bot.register_next_step_handler(message, handle_request_reason, intern_id=intern.pk, kind='return')


def handle_request_reason(message, intern_id, kind):
    from apps.interns import freeze
    from apps.interns.models import Intern

    text = (message.text or '').strip()
    if not text or text == CANCEL_BUTTON or text.startswith('/'):
        bot.send_message(message.chat.id, 'Заявка не отправлена.', reply_markup=types.ReplyKeyboardRemove())
        return
    intern = Intern.objects.filter(pk=intern_id).first()
    try:
        (freeze.request_freeze if kind == 'freeze' else freeze.request_return)(intern, text)
    except freeze.FreezeError as error:
        bot.send_message(message.chat.id, ' '.join(error.messages), reply_markup=types.ReplyKeyboardRemove())
        return
    bot.send_message(
        message.chat.id, 'Заявка отправлена руководителю. Решение придёт сюда.',
        reply_markup=types.ReplyKeyboardRemove(),
    )


def _projects_markup(projects):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    for project in projects:
        markup.add(types.KeyboardButton(project.name))
    markup.add(types.KeyboardButton(CANCEL_BUTTON))
    return markup


@bot.message_handler(commands=['projects'])
def handle_projects(message):
    """После одобренного возвращения — выбрать новый проект с открытым набором."""
    from apps.interns import freeze

    intern = _known_intern(message)
    if intern is None:
        return
    if freeze.is_pm_direction(intern):
        bot.send_message(message.chat.id, 'Проект для направления PM назначает руководитель.')
        return
    if freeze.open_return(intern) is None:
        bot.send_message(message.chat.id, 'Выбор проекта доступен после одобренного возвращения из заморозки.')
        return
    projects = freeze.available_projects(intern)
    if not projects:
        bot.send_message(message.chat.id, 'Сейчас нет проектов с открытым набором и свободным местом. '
                                          'Загляните позже: /projects.')
        return
    bot.send_message(message.chat.id, 'Выберите проект:', reply_markup=_projects_markup(projects))
    bot.register_next_step_handler(message, handle_project_choice, intern_id=intern.pk)


def handle_project_choice(message, intern_id):
    from apps.interns import freeze
    from apps.interns.models import Intern

    text = (message.text or '').strip()
    intern = Intern.objects.filter(pk=intern_id).first()
    if intern is None or text == CANCEL_BUTTON:
        bot.send_message(message.chat.id, 'Проект не выбран.', reply_markup=types.ReplyKeyboardRemove())
        return
    project = next((p for p in freeze.available_projects(intern) if p.name == text), None)
    if project is None:
        bot.send_message(message.chat.id, 'Такого проекта нет в списке — наберите /projects ещё раз.',
                         reply_markup=types.ReplyKeyboardRemove())
        return
    try:
        freeze.choose_project(intern, project)
    except freeze.FreezeError as error:
        bot.send_message(message.chat.id, ' '.join(error.messages), reply_markup=types.ReplyKeyboardRemove())
        return
    bot.send_message(message.chat.id, f'Готово! Вы в команде проекта «{project.name}».',
                     reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(commands=['leave'])
def handle_leave(message):
    """Выпускник в двух проектах просит выйти из одного (решает руководитель)."""
    from apps.interns import discipline
    from apps.teams.models import TeamMember

    intern = _known_intern(message)
    if intern is None:
        return
    if not discipline.leave_allowed(intern):
        bot.send_message(message.chat.id, 'Выход из одного проекта — только для выпускника в двух проектах. '
                                          'Чтобы приостановить стажировку, используйте /freeze.')
        return
    projects = [m.project for m in TeamMember.objects.filter(intern=intern, status=TeamMember.Status.ACTIVE,
                                                              project__isnull=False).select_related('project')]
    bot.send_message(message.chat.id, 'Из какого проекта выйти?', reply_markup=_projects_markup(projects))
    bot.register_next_step_handler(message, handle_leave_project, intern_id=intern.pk)


def handle_leave_project(message, intern_id):
    from apps.teams.models import TeamMember

    text = (message.text or '').strip()
    member = TeamMember.objects.filter(intern_id=intern_id, status=TeamMember.Status.ACTIVE,
                                       project__name=text).select_related('project').first()
    if member is None:
        bot.send_message(message.chat.id, 'Заявка не отправлена.', reply_markup=types.ReplyKeyboardRemove())
        return
    bot.send_message(message.chat.id, f'Напишите причину выхода из «{member.project.name}».',
                     reply_markup=_one_button(CANCEL_BUTTON))
    bot.register_next_step_handler(message, handle_leave_reason, intern_id=intern_id, project_id=member.project_id)


def handle_leave_reason(message, intern_id, project_id):
    from apps.interns import discipline
    from apps.interns.freeze import FreezeError
    from apps.interns.models import Intern
    from apps.projects.models import Project

    text = (message.text or '').strip()
    if not text or text == CANCEL_BUTTON or text.startswith('/'):
        bot.send_message(message.chat.id, 'Заявка не отправлена.', reply_markup=types.ReplyKeyboardRemove())
        return
    try:
        discipline.request_leave(Intern.objects.get(pk=intern_id), Project.objects.filter(pk=project_id).first(), text)
    except FreezeError as error:
        bot.send_message(message.chat.id, ' '.join(error.messages), reply_markup=types.ReplyKeyboardRemove())
        return
    bot.send_message(message.chat.id, 'Заявка отправлена руководителю. До решения участие сохраняется.',
                     reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_fallback(message):
    """Сообщение вне сценария (потерялся, написал не в ответ на вопрос
    бота) — раньше бот в этом случае просто молчал. register_next_step_handler
    перехватывает следующее сообщение раньше этого хендлера, так что сюда
    попадают только действительно «случайные» сообщения."""
    bot.send_message(
        message.chat.id,
        'Не понял сообщение. Чтобы начать сначала — наберите /start.',
    )

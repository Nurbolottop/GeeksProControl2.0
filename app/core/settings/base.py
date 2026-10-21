from dotenv import load_dotenv
from pathlib import Path
import os

load_dotenv()

# =============================================================================
# PATHS
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# =============================================================================
# SECURITY
# =============================================================================
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    raise Exception("SECRET_KEY не задан в переменных окружения")

_allowed_hosts_env = os.getenv('ALLOWED_HOSTS', '').strip()
ALLOWED_HOSTS = [host.strip() for host in _allowed_hosts_env.split(',') if host.strip()]

_csrf_trusted_origins_env = os.getenv('CSRF_TRUSTED_ORIGINS', '').strip()
CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in _csrf_trusted_origins_env.split(',') if origin.strip()
]

# Публичный адрес сайта — нужен там, где нет request (например, боту
# в Telegram) для ссылок на публичные формы.
SITE_URL = os.getenv('SITE_URL', 'http://localhost:8000').rstrip('/')

# Токен бота-выпускника (apps.graduate_bot) — пусто, если бот не настроен.
# В 2.0 здесь только собственный бот версии 2.0 (в разработке — тестовый),
# никогда не токен действующего бота 1.0: см. PROTECTED_VALUE_HASHES.
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')

# Кому бот вообще может писать (apps.common.outbound):
#   off       — никому: сообщения только пишутся в лог (по умолчанию);
#   allowlist — только чатам из TELEGRAM_TEST_CHAT_IDS (свои тестовые аккаунты);
#   on        — всем (только production 2.0 после переноса данных).
TELEGRAM_OUTBOUND_MODE = os.getenv('TELEGRAM_OUTBOUND_MODE', 'off').strip().lower()

# Где работает этот экземпляр: development, stage или production.
# До утверждённой финальной миграции 2.0 — только development/stage:
# там режим `on` запрещён системной проверкой gp2.E003, и реальные
# стажёры сообщений получить не могут.
GP2_ENVIRONMENT = os.getenv('GP2_ENVIRONMENT', 'development').strip().lower()
TELEGRAM_TEST_CHAT_IDS = {
    chat_id.strip() for chat_id in os.getenv('TELEGRAM_TEST_CHAT_IDS', '').split(',')
    if chat_id.strip()
}
# Тестовые группы (chat_id супергрупп, вида -100…): только в них вне
# production бот может писать и создавать темы (apps.common.telegram_guard).
TELEGRAM_TEST_GROUP_IDS = {
    chat_id.strip() for chat_id in os.getenv('TELEGRAM_TEST_GROUP_IDS', '').split(',')
    if chat_id.strip()
}
# Username бота без «@» — для ссылок t.me/<username> (не секрет).
TELEGRAM_BOT_USERNAME = os.getenv('TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')

# SHA-256 значений, которые 2.0 использовать запрещено: токен бота 1.0,
# ID рабочей Google-таблицы 1.0. Сами значения в код не попадают — только
# хеши, через запятую. Совпадение — бот не стартует, выгрузка отключается,
# `manage.py check` падает (apps.common.safety).
PROTECTED_VALUE_HASHES = {
    value.strip().lower() for value in os.getenv('PROTECTED_VALUE_HASHES', '').split(',')
    if value.strip()
}

# Если с сервера нет прямого маршрута до Telegram (бывает — плохой пиринг
# дата-центра), сюда кладут адрес SOCKS5/HTTP-релея, например
# socks5h://host.docker.internal:40001. Пусто — бот ходит напрямую.
TELEGRAM_PROXY_URL = os.getenv('TELEGRAM_PROXY_URL', '')

# Общая Google-таблица резерва кадров: кандидаты попадают в неё сами,
# отсортированные по направлениям (apps.reserve.gsheets). Нужен ключ
# сервисного аккаунта, которому таблица открыта на редактирование; без
# ключа или ID таблицы выгрузка просто не работает, платформа этого не
# замечает. Значения по умолчанию нет намеренно: рабочая таблица 1.0 не
# должна получать данные из 2.0.
GOOGLE_SHEETS_CREDENTIALS_FILE = os.getenv('GOOGLE_SHEETS_CREDENTIALS_FILE', '')
RESERVE_SHEET_ID = os.getenv('RESERVE_SHEET_ID', '')
# Пусто — пишем в первый лист таблицы.
RESERVE_SHEET_TAB = os.getenv('RESERVE_SHEET_TAB', '')

# Excel-файлы для разовых импортов (import_people) лежат
# вне репозитория и Docker-образа: в них персональные данные.
IMPORT_DATA_DIR = Path(os.getenv('IMPORT_DATA_DIR', BASE_DIR / 'data'))

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
# Свои имена cookies: 1.0 и 2.0 живут на соседних поддоменах одного
# домена, и одинаковые sessionid/csrftoken легко перепутать.
SESSION_COOKIE_NAME = os.getenv('SESSION_COOKIE_NAME', 'gp2_sessionid')
CSRF_COOKIE_NAME = os.getenv('CSRF_COOKIE_NAME', 'gp2_csrftoken')

# =============================================================================
# APPLICATIONS
# =============================================================================

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    # Third-party
    'django_htmx',

    # Local apps
    'apps.accounts',
    'apps.common',
    'apps.clients',
    'apps.projects',
    'apps.dashboard',
    'apps.tasks',
    'apps.teams',
    'apps.interns',
    'apps.reserve',
    'apps.training',
    'apps.documents',
    'apps.meetings',
    'apps.notifications',
    'apps.risks',
    'apps.audit',
    'apps.resources',
    'apps.reports',
    'apps.flows',
    'apps.attendance',
    'apps.dailycheck',
    'apps.pm_portal',
    'apps.lead_portal',
    'apps.scripts',
    'apps.graduate_bot',
    'apps.tgbot',
    'apps.video',
    'apps.ui',
]

# =============================================================================
# MIDDLEWARE
# =============================================================================

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # Default deny: стажёр и неизвестные роли — только публичные страницы
    'apps.accounts.middleware.RoleAccessMiddleware',
    'apps.accounts.middleware.PMScopeMiddleware',
    'apps.accounts.middleware.LeadScopeMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
]

# =============================================================================
# URLS & WSGI
# =============================================================================

ROOT_URLCONF = 'core.urls'
WSGI_APPLICATION = 'core.wsgi.application'

# =============================================================================
# TEMPLATES
# =============================================================================

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.notifications.context_processors.notifications',
                'apps.training.context_processors.academy_nav',
                'apps.ui.context_processors.ui',
            ],
        },
    },
]

# =============================================================================
# DATABASE
# =============================================================================

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('POSTGRES_DB'),
        'USER': os.getenv('POSTGRES_USER'),
        'PASSWORD': os.getenv('POSTGRES_PASSWORD'),
        'HOST': os.getenv('POSTGRES_HOST'),
        'PORT': int(os.getenv('POSTGRES_PORT', 5432)),
    }
}

# =============================================================================
# AUTH
# =============================================================================

AUTH_USER_MODEL = 'accounts.User'

# Телефон логином записан по-разному (+996…, 996…, 0…), а человек вводит
# его как помнит — PhoneBackend сверяет последние девять цифр, чтобы
# формат не мешал войти. Обычный вход по логину он тоже умеет.
AUTHENTICATION_BACKENDS = ['apps.accounts.backends.PhoneBackend']
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# =============================================================================
# INTERNATIONALIZATION
# =============================================================================

LANGUAGE_CODE = os.getenv('LANGUAGE_CODE', 'ru')
TIME_ZONE = os.getenv('TIME_ZONE', 'Asia/Bishkek')
USE_I18N = True
USE_TZ = True

# =============================================================================
# STATIC & MEDIA FILES
# =============================================================================

STATIC_URL = '/static/'

STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static'),
]
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

MEDIA_URL = '/media/'
MEDIA_ROOT = os.getenv('MEDIA_ROOT', os.path.join(BASE_DIR, 'media'))

# В media лежат договоры и резюме, поэтому публично nginx их не отдаёт:
# каждый запрос /media/… проходит через apps.common.media (проверка прав),
# а сам файл nginx отдаёт из internal-location по X-Accel-Redirect.
# Пусто — Django отдаёт файл сам (разработка без nginx).
MEDIA_ACCEL_REDIRECT_PREFIX = os.getenv('MEDIA_ACCEL_REDIRECT_PREFIX', '')
# Что можно открыть без входа: фото кандидатов показываются на публичной
# витрине резерва (/candidate/<token>/). Всё остальное — только после входа.
PUBLIC_MEDIA_PREFIXES = ('reserve/photo/',)

# Ограничения загружаемых файлов (ТЗ §35)
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024  # 20 MB
ALLOWED_UPLOAD_EXTENSIONS = [
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'txt',
    'png', 'jpg', 'jpeg', 'webp', 'zip',
]

# =============================================================================
# CELERY
# =============================================================================

CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.getenv('REDIS_URL', 'redis://redis:6379/0')
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True

# Расписание автоматических проверок (ТЗ §22, §37)
from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    'daily-checks': {
        'task': 'apps.notifications.tasks.run_daily_checks',
        'schedule': crontab(hour=7, minute=0),
    },
    'weekly-metrics': {
        'task': 'apps.reports.tasks.generate_weekly_metrics',
        'schedule': crontab(hour=6, minute=30, day_of_week=1),  # понедельник
    },
    'monthly-kpi': {
        'task': 'apps.reports.tasks.generate_monthly_kpi_snapshot',
        'schedule': crontab(hour=6, minute=0, day_of_month=1),
    },
    # Выпускники, которые не сделали выбор, и те, кто давно ждёт проект.
    'remind-graduates': {
        'task': 'apps.graduate_bot.tasks.remind_graduates',
        'schedule': crontab(hour=9, minute=0),
    },
    # Очередь исходящих Telegram (этап 8): повторы временных ошибок.
    # Вне разрешённого режима сообщения остаются «Задержано защитой».
    'telegram-outbox': {
        'task': 'apps.tgbot.tasks.flush_outbox',
        'schedule': crontab(minute='*/2'),
    },
}

# Пороговые значения проверок (ТЗ §22)
DEADLINE_RISK_DAYS = 7
DEADLINE_RISK_PROGRESS = 70
DEADLINE_HIGH_RISK_DAYS = 3
DEADLINE_HIGH_RISK_PROGRESS = 90
PROJECT_INACTIVE_DAYS = 3

# Подпись напоминаний ПМ об актуальности этапа: «<имя> просит обновить этап…»
STAGE_REMINDER_FROM = os.getenv('STAGE_REMINDER_FROM', 'Руководитель')

# =============================================================================
# LOGGING
# =============================================================================

# Только stdout: логи собирает Docker (`docker compose -p geekspro2 logs`),
# метка gp2 отличает их от 1.0 в общих системах сбора.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[gp2] [{asctime}] {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': os.getenv('LOG_LEVEL', 'INFO'),
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}

# =============================================================================
# DEFAULTS
# =============================================================================

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Отдел продаж (UI-04C): Telegram id сотрудников, чьи подтверждения принимаются
# (apps.interns.sales). Пусто — подтверждения продаж не принимаются вовсе.
SALES_AGENT_TELEGRAM_IDS = {x.strip() for x in os.getenv('SALES_AGENT_TELEGRAM_IDS', '').split(',') if x.strip()}
# Видеособрания (этап 9). VIDEO_PROVIDER: simulator (по умолчанию — локальный
# симулятор, без сети) или jaas (Jitsi as a Service, 8x8). Секреты — только из
# окружения: ключ JaaS — файлом (JAAS_PRIVATE_KEY_PATH), не значением.
VIDEO_PROVIDER = os.getenv('VIDEO_PROVIDER', 'simulator').strip().lower()
JAAS_APP_ID = os.getenv('JAAS_APP_ID', '').strip()
JAAS_KEY_ID = os.getenv('JAAS_KEY_ID', '').strip()
JAAS_PRIVATE_KEY_PATH = os.getenv('JAAS_PRIVATE_KEY_PATH', '').strip()
JAAS_WEBHOOK_SECRET = os.getenv('JAAS_WEBHOOK_SECRET', '').strip()
VIDEO_SIMULATOR_SECRET = os.getenv('VIDEO_SIMULATOR_SECRET', '').strip()
# Release-gate (этап 10A): ставится в 1 только после ручной проверки в консоли JaaS,
# что анонимный вход (без JWT) в комнаты приложения запрещён.
JAAS_ANONYMOUS_BLOCKED_VERIFIED = os.getenv('JAAS_ANONYMOUS_BLOCKED_VERIFIED', '').strip() in ('1', 'true', 'yes')

# Группа отдела продаж (этап 8A): только из неё принимаются сообщения «#оплата».
# Пусто — подтверждения продаж из Telegram не принимаются.
SALES_TELEGRAM_CHAT_ID = os.getenv('SALES_TELEGRAM_CHAT_ID', '').strip()

# Тесты: загрузки — во временную папку (core/test_runner.py)
TEST_RUNNER = 'core.test_runner.TempMediaRunner'

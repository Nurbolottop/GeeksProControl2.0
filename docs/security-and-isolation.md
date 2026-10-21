# Изоляция от 1.0 и безопасность

GeeksPro Control 1.0 — действующая система. 2.0 начиналась как её копия,
поэтому всё, что могло указывать на ресурсы 1.0, отделено явно.

## Карта ресурсов

| Ресурс | 1.0 (не трогать) | 2.0 |
|---|---|---|
| Git | `Nurbolottop/GeeksProControl` | `Nurbolottop/GeeksProControl2.0`, новая история |
| Каталог на сервере | `/root/GeeksProControl` | `/opt/geekspro2` |
| Compose-проект | `geekspro` | `geekspro2` (всегда `-p geekspro2`) |
| Том PostgreSQL | `geekspro_postgres_data` | `geekspro2_postgres_data` (dev: `geekspro2_dev_postgres_data`) |
| Сеть | `geekspro_default` | `geekspro2_internal` (dev: `geekspro2_dev_internal`) |
| Порт web | 127.0.0.1:8087 | 127.0.0.1:8100 |
| Порты БД/Redis | не публикуются | не публикуются (dev: только 127.0.0.1:5447/6393) |
| Домен | geekspro.zeastudio.su | gpro.zeastudio.su |
| nginx | `sites-available/geekspro.zeastudio.su` | `sites-available/gpro.zeastudio.su` |
| Static / media | `/root/GeeksProControl/app/…` | `/opt/geekspro2/storage/{static,media}` |
| Cookies | `sessionid`, `csrftoken` | `gp2_sessionid`, `gp2_csrftoken` |
| SECRET_KEY, пароли БД | свои | новые, сгенерированы заново |
| Telegram-бот | боевой бот | отдельный тестовый бот 2.0 |
| Google-таблица резерва | рабочая таблица | только тестовая таблица 2.0 |
| Данные | рабочие, единственный источник | синтетические (`seed_demo`); перенос — только по [migration-plan.md](migration-plan.md) |

Ни один том и ни одна сеть 2.0 не объявлены `external`, `container_name`
не задаётся: compose физически не может подхватить ресурсы 1.0.

`COMPOSE_PROJECT_NAME` из `.env` сильнее `name:` в compose-файле — поэтому
`scripts/gp2-compose.sh` всегда передаёт `-p geekspro2` и прерывается, если
в `.env` другое имя проекта.

## Модель доступа к веб-интерфейсу

| Роль | Что доступно | Где проверяется |
|---|---|---|
| Руководитель, администратор, superuser | вся платформа | `apps/accounts/access.py` → `has_full_access` |
| ПМ | только `/pm/`, свои проекты и их объекты | `PMScopeMiddleware`, `pm_project_or_404`, выборки `pk=…, project=project` |
| Тимлид | только `/lead/`, свои проекты, своё направление | `LeadScopeMiddleware`, `lead_project_or_404`, `_own_direction_*` |
| Стажёр, пустая или неизвестная роль | **веб-кабинета нет**: только публичные формы, вход/выход | `RoleAccessMiddleware` — default deny, 403 на любой GET/POST |
| Аноним | публичные формы по ссылкам; остальное — на вход | `login_required` + `RoleAccessMiddleware` |

- `RoleAccessMiddleware` стоит сразу после аутентификации: закрыто всё, что не в
  `PUBLIC_PREFIXES`, независимо от декораторов конкретной страницы. Флаг `is_staff`
  сам по себе прав не даёт.
- Файлы `/media/` проверяются по конкретному файлу (`apps/common/media.can_open`):
  ПМ — документы своих проектов, тимлид — своя карточка резерва, остальным — 404.
- Каталог `/ui/` — только полный доступ и только вне production (в production — 404).
- Регрессия: `apps/accounts/test_access.py` обходит **все** маршруты urlconf
  от лица стажёра, неизвестной и пустой роли (GET и POST) — новая незакрытая
  страница сразу роняет тест.

## Защита от значений 1.0 (`PROTECTED_VALUE_HASHES`)

В `.env` 2.0 хранятся SHA-256 токена бота 1.0 и ID рабочей таблицы 1.0
(не сами значения). Если такое значение снова окажется в настройках:

- `manage.py check` / `runserver` / `migrate` падают с `gp2.E001`;
- модуль бота не импортируется (`ImproperlyConfigured`), `run_graduate_bot` не стартует;
- выгрузка в Google Sheets отключается, `push()` бросает исключение;
- `scripts/gp2-compose.sh` не запускает compose.

Хеш считается так: `printf '%s' "<значение>" | shasum -a 256`.

## Telegram

Тестовый бот 2.0: **@GeeksProAutoBot** (`TELEGRAM_BOT_USERNAME`). Токен — только в
локальном `.env` (права 600, игнорируется Git); вводится командой
`python3 app/tools/set_telegram_token.py` скрытым вводом (не через аргументы, не в историю
терминала), с проверкой формата и запретом токена бота 1.0 — без запросов в Telegram.

Две линии защиты:

1. **`GuardedTeleBot.send_message`** (`apps/common/outbound.py`) — в `off` сообщение не
   отправляется, а пишется в лог и `OUTBOX` (заглушка, сценарии не ломаются).
2. **`telegram_guard`** (`apps/common/telegram_guard.py`) — хук `apihelper.CUSTOM_REQUEST_SENDER`,
   через который идёт **каждый** запрос к Bot API в любом процессе (web, celery, бот):

| Режим | Что разрешено |
|---|---|
| `off` (по умолчанию) | ничего — даже `getMe` и `getUpdates`; бот не запускается |
| `allowlist` | `getMe`/`getUpdates`/`deleteWebhook`; сообщения — только чатам из `TELEGRAM_TEST_CHAT_IDS` и `TELEGRAM_TEST_GROUP_IDS`; темы — только в группах из `TELEGRAM_TEST_GROUP_IDS`; `setWebhook` и вызовы без адресата — запрещены |
| `on` | только при `GP2_ENVIRONMENT=production` (иначе ошибка запуска `gp2.E003` и блок запросов); темы — по-прежнему только в известных группах |

- Реальные стажёры не попадают в allowlist — значит не получают сообщений вне production.
- Polling запускается только вручную (`--profile bot`) и не в режиме `off`; webhook вне production
  не включается.
- `manage.py telegram_check` — офлайн-проверка настроек (токен не печатается).
- Системные проверки: формат токена (`gp2.E005`), username (`gp2.E006`), id пользователей и
  групп (`gp2.E007`/`E008`), пустой allowlist (`gp2.W001`), токен 1.0 (`gp2.E001`).
- Группы проектов (будущее): руководитель создаёт супергруппу с темами, добавляет
  @GeeksProAutoBot администратором, подтверждает в Control — только после этого группа станет
  «известной» и бот сможет создать темы. До модуля групп «известные» — это тестовые группы из
  `TELEGRAM_TEST_GROUP_IDS`.

## Google Sheets

- `RESERVE_SHEET_ID` без значения по умолчанию; пусто — выгрузка выключена.
- Ключ сервисного аккаунта — файл в `secrets/` на сервере, монтируется в контейнер; в Git и образ не попадает (`.gitignore`, `.dockerignore`).
- Для 2.0 — отдельная таблица и, желательно, отдельный сервисный аккаунт. Боевой ключ 1.0 из копии удалён.

Других внешних интеграций (почта, платежи, сторонние API) в коде нет.

## Файлы и media

- В media лежат договоры, резюме, вложения задач. Прямой публичной раздачи нет:
  `/media/…` обрабатывает `apps/common/media.py` — вошедшему пользователю файл
  отдаётся, анонимному только `reserve/photo/` (фото на публичной витрине кандидата).
- В production файл отдаёт nginx из `internal`-location `/protected-media/` по `X-Accel-Redirect`.
- На время разработки весь `gpro.zeastudio.su` дополнительно закрыт Basic Auth в nginx.

## Что не попадает в Git и Docker-образ

`.env*` (кроме `*.example`), `app/secrets/`, ключи (`*.pem`, `*.key`, service-account JSON),
`app/media/`, `storage/`, `app/staticfiles/`, `app/data/` и любые `*.xlsx/*.xls/*.csv/*.docx`,
SQL-дампы и бэкапы, `.claude/`, `.htpasswd`. Глобального игнора `*.json` нет —
исключаются только чувствительные файлы адресно.

Образ проверен: процессы работают от `app` (uid 10001), в образе нет `.env`,
ключей, xlsx, дампов, `secrets/`, `data/` и media.

## Импорт Excel

`import_people` берёт файл из `IMPORT_DATA_DIR` или `--file`.
Таблицы с персональными данными хранятся вне репозитория.

## Что сознательно не перенесено из 1.0

- Одноразовые команды правки данных 1.0 с реальными ФИО, телефонами, email,
  Telegram или ID записей базы 1.0 (`add_interns`, `fix_names`, `fix_oba_team`,
  `fix_omur_team`, `merge_intern_duplicates`, `assign_intern_projects`,
  `seed_geekspro`). Они уже отработали на 1.0 и остаются в её истории.
- Телефоны в тестах и docstring, похожие на реальные, заменены синтетическими (`+996700…`).
- Git-история 1.0 (вместе с xlsx, которые в ней были).

# GeeksPro Control 2.0

Внутренняя система GeeksPro: проекты, команды, стажёры, резерв кадров,
документы, риски и отчётность. Версия 2.0 — независимый проект с
собственной историей, окружением и развёртыванием. Действующая версия 1.0
(`geekspro.zeastudio.su`) работает отдельно и этим репозиторием не затрагивается.

- **Backend:** Python 3.11, Django 5.2, PostgreSQL 16, Redis 7, Celery + Beat
- **Frontend:** Django Templates + HTMX + Alpine.js, новая дизайн-система ([docs/design-system.md](docs/design-system.md))
- **Инфраструктура:** Docker Compose, Gunicorn, nginx на хосте
- **Архитектура:** модульный монолит, service layer + selectors

## Быстрый старт (разработка)

```bash
cp .env.example .env          # заполнить SECRET_KEY, пароли; НЕ копировать .env из 1.0
scripts/gp2-compose.sh up -d --build
open http://127.0.0.1:8100
```

Всегда запускайте compose через `scripts/gp2-compose.sh`: он задаёт проект
`geekspro2` явно и отказывается стартовать, если в `.env` оказались значения 1.0.

| Сервис | Адрес на хосте |
|---|---|
| web (runserver) | 127.0.0.1:8100 |
| PostgreSQL | 127.0.0.1:5447 (база `geekspro2_dev`) |
| Redis | 127.0.0.1:6393 |

Telegram-бот в разработке по умолчанию не запускается и никому не пишет
(`TELEGRAM_OUTBOUND_MODE=off`). Запуск только с тестовым ботом:
`scripts/gp2-compose.sh --profile bot up -d bot`.

```bash
# полный набор — параллельно (tblib из requirements-dev.txt, только dev-образ);
# если параллельный режим сбоит — тот же запуск без --parallel
scripts/gp2-compose.sh run --rm -e RUN_MIGRATIONS=0 web python manage.py test --noinput --parallel 4
# адресные тесты во время разработки — с сохранением тестовой БД
scripts/gp2-compose.sh run --rm -e RUN_MIGRATIONS=0 web python manage.py test --noinput --keepdb apps.interns.test_ui04c
scripts/gp2-compose.sh run --rm web python manage.py makemigrations --check
```

После изменения `requirements*.txt` или `docker/Dockerfile`: `scripts/gp2-compose.sh build web`.

## Структура

```
app/
  core/            settings (base/dev/prod), urls, celery
  apps/            24 модуля: accounts, projects, teams, interns, reserve, training,
                   meetings, attendance, documents, flows, reports, risks, audit,
                   notifications, pm_portal, lead_portal, graduate_bot, common…
  templates/       Django-шаблоны (переводятся на новую дизайн-систему)
  static/gp2/      дизайн-система 2.0 (tokens.css → …)
docker/            Dockerfile, gunicorn.conf.py
deploy/nginx/      конфигурация gpro.zeastudio.su
scripts/           entrypoint.sh, gp2-compose.sh
docs/              документация
```

## Документация

- [docs/security-and-isolation.md](docs/security-and-isolation.md) — изоляция от 1.0, секреты, Telegram, Google Sheets, media
- [docs/deployment.md](docs/deployment.md) — развёртывание на сервере, `gpro.zeastudio.su`
- [docs/design-system.md](docs/design-system.md) — дизайн-система и адаптивный интерфейс
- [docs/ui-implementation-plan.md](docs/ui-implementation-plan.md) — архитектура UI, компоненты, порядок работ
- [docs/migration-plan.md](docs/migration-plan.md) — финальная миграция 1.0 → 2.0 (только по разрешению руководителя)
- [docs/integrations.md](docs/integrations.md) — Telegram-группы проектов, переход бота, видеособрания (JaaS)

## Правила

- Миграции хранятся в репозитории и не пересоздаются; на сервере только `migrate`.
- Секреты — только в `.env` и `secrets/` вне Git и Docker-образа.
- Импортные Excel-файлы с персональными данными — в `IMPORT_DATA_DIR`, не в репозитории.

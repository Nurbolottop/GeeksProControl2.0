# Runbook staging Control 2.0 — https://gpro.zeastudio.su

> Любое действие на сервере (72.56.116.115) — **только с отдельного разрешения
> владельца**. Staging работает только на синтетических данных. Control 1.0
> (`/root/GeeksProControl`, проект `geekspro`, порт 8087) не трогается.

Обозначения: ✅ реализовано и локально проверено · 🖥 требует проверки на VPS ·
🔐 требует разрешения пользователя · ⛔ блокирует production.

## 0. Изоляция от 1.0 (проверено локально, этап 11A)

| Ресурс | 1.0 | 2.0 |
|---|---|---|
| Каталог | `/root/GeeksProControl` | `/opt/geekspro2` |
| Compose project | `geekspro` | `geekspro2` (`scripts/gp2-compose.sh` проверяет `.env`) |
| Web-порт | 127.0.0.1:8087 | 127.0.0.1:8100 |
| PostgreSQL / Redis | свои контейнеры | свои, **без публикации портов** |
| Том БД | `geekspro_postgres_data` | `geekspro2_postgres_data` |
| Сеть | сеть проекта 1.0 | `geekspro2_internal` |
| Media / static | `app/media` 1.0 | `/opt/geekspro2/storage/{media,static}` |
| Cookies | 1.0 | `gp2_sessionid`, `gp2_csrftoken`, свой `SECRET_KEY` |
| Telegram | бот 1.0 | только @GeeksProAutoBot, отдельный токен, `TELEGRAM_OUTBOUND_MODE=off/allowlist` |
| Nginx | `geekspro.zeastudio.su` | отдельный server `gpro.zeastudio.su` (Basic Auth, noindex) |
| Резервные копии | 1.0 | `/opt/geekspro2/backups` (`scripts/backup.sh`) |

Проверка конфигурации на любой машине с Docker (без сети, без контейнеров):
```bash
scripts/check-compose.sh        # изоляция, профили, один polling, лимиты, шаблон stage
```

## 1. Ресурсы и лимиты

Сервер: 3.8 ГБ ОЗУ, свободно ~1.4 ГБ (docs/deployment.md). Измерено локально на
production-образе (синтетика, простой): web 154 МБ, celery 92 МБ, beat 78 МБ,
db 20 МБ, redis 10 МБ, nginx 9 МБ.

| Сервис | mem_limit (переопределение в .env) | cpus |
|---|---|---|
| db | `DB_MEM_LIMIT=384m` (shared_buffers 96MB, max_connections 40) | `DB_CPUS=1.0` |
| redis | 96m (maxmemory 64mb, без persistence) | — |
| web | `WEB_MEM_LIMIT=384m`, `GUNICORN_WORKERS=2` | `WEB_CPUS=1.0` |
| celery | `CELERY_MEM_LIMIT=256m`, `CELERY_CONCURRENCY=1` | `CELERY_CPUS=0.5` |
| celery-beat | 128m | 0.25 |
| bot (профиль) | 160m | 0.25 |

Сумма: 1248 МБ без бота, 1408 МБ с ботом. Swap контейнерам не даётся,
`oom_score_adj=500` — при нехватке памяти ядро первым завершит процессы 2.0.
Диск: образ ~600 МБ, БД staging < 100 МБ, бэкапы ×14 дней.

**Риск:** при одновременном пике 1.0 и 2.0 запас памяти сервера ~0. Если `free -h`
показывает < 300 МБ свободных или в `dmesg` появляется OOM — staging остановить
(`stop`). **Безопасная альтернатива:** отдельный VPS 2 ГБ для staging (рекомендуется
для фазы активной проверки), на общем сервере — только после замеров.

## 2. Установка (🖥 🔐)

```bash
mkdir -p /opt/geekspro2 && cd /opt/geekspro2
git clone git@github.com:Nurbolottop/GeeksProControl2.0.git .   # deploy key, read-only; репозиторий ПРИВАТНЫЙ
cp .env.prod.example .env && chmod 600 .env
# заполнить: SECRET_KEY, POSTGRES_PASSWORD (новые, случайные), ALLOWED_HOSTS=gpro.zeastudio.su,
# CSRF_TRUSTED_ORIGINS=https://gpro.zeastudio.su, SITE_URL=https://gpro.zeastudio.su,
# GP2_ENVIRONMENT=stage, TELEGRAM_OUTBOUND_MODE=off, VIDEO_PROVIDER=simulator
mkdir -p storage/static storage/media secrets backups && chown -R 10001:10001 storage
scripts/check-compose.sh
scripts/gp2-compose.sh --prod config --quiet
scripts/gp2-compose.sh --prod build
scripts/gp2-compose.sh --prod run --rm web python manage.py check --deploy
```
Ожидаемые предупреждения `check --deploy`: только `security.W004` (HSTS — включить
после проверки сертификата) и `W008` (редирект на HTTPS делает nginx).

## 3. Nginx, DNS, HTTPS (🖥 🔐)

1. DNS: A `gpro.zeastudio.su → 72.56.116.115` (проверить `dig +short @8.8.8.8 и @1.1.1.1`).
2. Bootstrap: `deploy/nginx/gpro.zeastudio.su.bootstrap.conf` → `nginx -t` → `systemctl reload nginx`
   → `certbot certonly --webroot -w /var/www/certbot -d gpro.zeastudio.su`.
3. Основной конфиг: `deploy/nginx/gpro.zeastudio.su.conf`; `htpasswd -c /etc/nginx/geekspro2.htpasswd <логин>`;
   `nginx -t` **обязательно** до reload (ошибка в одном server ломает reload и 1.0).
4. Конфиг 1.0 не меняется. Проверка: `curl -sI https://geekspro.zeastudio.su` — как до изменений.

## 4. Запуск, остановка, перезапуск

```bash
scripts/gp2-compose.sh --prod up -d                 # db, redis, web, celery, celery-beat
scripts/gp2-compose.sh --prod ps                    # web, celery, db, redis — healthy
scripts/gp2-compose.sh --prod stop                  # остановка БЕЗ удаления данных
scripts/gp2-compose.sh --prod restart web           # перезапуск сервиса
# НИКОГДА: docker compose down -v, docker volume prune, docker system prune --volumes
```
Локально проверено (этап 11A): после `restart` всех сервисов готовность через 6 с;
после `down` + `up` данные сохранены; при остановке БД `/readyz/` отвечает 503 и
восстанавливается сам.

## 5. Проверка после запуска

```bash
curl -s -u '<basic>' https://gpro.zeastudio.su/healthz/   # ok — процесс жив (на staging весь сайт за Basic Auth)
curl -s -u '<basic>' https://gpro.zeastudio.su/readyz/    # {"db": "ok", "cache": "ok"}
# Синтетика: seed_demo + тестовые руководитель/ПМ/тимлид (пароли — только у проверяющего)
scripts/gp2-compose.sh --prod exec web python manage.py seed_demo
GP2_SMOKE_BASE=https://gpro.zeastudio.su GP2_SMOKE_BASIC='<логин:пароль>' GP2_SMOKE_FORWARDED=0 \
  GP2_SMOKE_USERS='head=…,pm=…,lead=…' GP2_SMOKE_MEDIA='<путь документа>' python3 scripts/smoke_check.py
```
Критерий: «Проверок: 31, ошибок: 0» (локально на production-образе за nginx — пройдено).
Планировщик: в логах `celery-beat` — `Scheduler: Sending due task telegram-outbox`;
`celery -A core inspect registered` — 5 периодических задач.

## 6. Резервные копии

```bash
DC="scripts/gp2-compose.sh --prod" MEDIA_DIR=/opt/geekspro2/storage/media \
  BACKUP_DIR=/opt/geekspro2/backups KEEP_DAYS=14 scripts/backup.sh --verify
```
Ежедневно через cron хоста (🔐). `--verify` восстанавливает дамп в одноразовую базу
`gp2_backup_check` и сверяет суммы (локально проверено). Копию вне сервера — отдельно.

## 7. Диагностика

| Симптом | Команда |
|---|---|
| Контейнер не healthy | `scripts/gp2-compose.sh --prod logs --tail 200 <сервис>` |
| 502 от nginx | `curl -s -H 'Host: gpro.zeastudio.su' -H 'X-Forwarded-Proto: https' http://127.0.0.1:8100/readyz/` |
| Память | `docker stats --no-stream`, `free -h`, `dmesg -T | grep -i oom` |
| Миграции | `… exec web python manage.py showmigrations | grep '\[ \]'` — пусто |
| Telegram | Control → «Telegram-бот» → журнал исходящих; `… exec web python manage.py telegram_check` |
| Задачи Celery | `… exec celery celery -A core inspect active` |

## 8. Внешние проверки — пошагово (🔐 каждая — отдельным разрешением)

### Telegram
1. Токен @GeeksProAutoBot — у @BotFather (`/token`); ввод на сервере **скрыто**:
   `python3 app/tools/set_telegram_token.py` (не в аргументах, не в истории, не в чат).
2. Свои User ID — через @userinfobot; записать в `TELEGRAM_TEST_CHAT_IDS`.
3. Тестовая супергруппа с темами: создать в Telegram, включить «Темы», добавить бота
   администратором с правом «Управление темами»; её `chat_id` (−100…) — в `TELEGRAM_TEST_GROUP_IDS`.
4. Отдельная тестовая группа продаж: `SALES_TELEGRAM_CHAT_ID`, свой id — `SALES_AGENT_TELEGRAM_IDS`.
5. `TELEGRAM_OUTBOUND_MODE=allowlist`; `scripts/gp2-compose.sh --prod --profile bot up -d bot`
   — **один** процесс (второй не стартует из-за замка polling; бот 1.0 этим токеном не пользуется).
6. Проверить: `/start` → постоянная клавиатура не исчезает; регистрация до «Ожидает отдела продаж»;
   `#оплата` в группе продаж; `/bind КОД` и подтверждение в Control → темы; задача → «Отправить работу».
7. Остановка: `… stop bot`. Критерий: в журнале «Отправлено» только тестовым чатам, посторонние — «skipped».

### JaaS (⛔ до практического подтверждения запрета анонимного входа)
1. Тестовый аккаунт jaas.8x8.vc; AppID; сгенерировать ключ, загрузить публичный, `kid` → `JAAS_KEY_ID`.
2. Приватный ключ — файлом вне репозитория: `/opt/geekspro2/secrets/jaas.pem`, `chmod 600`, `JAAS_PRIVATE_KEY_PATH`.
3. В консоли — требование JWT для всех участников. **Проверка:** открыть
   `https://8x8.vc/<AppID>/<комната>` без `?jwt=` из чистого браузера — вход должен быть невозможен.
   Только после этого `JAAS_ANONYMOUS_BLOCKED_VERIFIED=1`.
4. Вебхук: `https://gpro.zeastudio.su/video/webhook/jaas/` — в конфиге nginx уже исключён из
   Basic Auth (подлинность — подпись `X-Jaas-Signature`), секрет → `JAAS_WEBHOOK_SECRET`.
5. `VIDEO_PROVIDER=jaas`; тестовое собрание с двумя тестовыми участниками; вход кнопкой бота.
6. Пересланная ссылка: переслать приглашение другому аккаунту — входа нет; одноразовый код
   повторно — 403.
7. Сверка: события в карточке собрания совпадают с фактическими входами/выходами.

### GitHub
* Репозиторий **Control 2.0 по последней проверке публичный — push запрещён до перевода в private.**
* Отдельно: репозиторий **Control 1.0** содержит исторические персональные данные — его
  тоже нужно проверить на видимость и сделать приватным владельцу (2.0 его не меняет).

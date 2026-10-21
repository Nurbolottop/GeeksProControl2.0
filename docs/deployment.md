# Развёртывание GeeksPro Control 2.0

> **Тестовый деплой (stage), данные 1.0 не переносятся.** 2.0 работает на
> собственной PostgreSQL с синтетическими данными (`seed_demo`), без прямого
> подключения к базе 1.0 и без общих томов. Реальный бот не запускается,
> стажёрам ничего не отправляется. 1.0 остаётся единственным источником
> рабочих данных. Финальная миграция — отдельный план
> ([migration-plan.md](migration-plan.md)) и только по разрешению руководителя.

Сервер: `72.56.116.115` (Ubuntu, nginx 1.28 на хосте, Docker Compose 2.40).
На нём же работают 1.0 (`/root/GeeksProControl`, проект `geekspro`, порт 8087)
и другие проекты. **Конфигурацию, контейнеры, тома и nginx-конфиг 1.0 не менять.**

| | Значение |
|---|---|
| Домен | `gpro.zeastudio.su` |
| Каталог | `/opt/geekspro2` |
| Compose-проект | `geekspro2` |
| Порт web | `127.0.0.1:8100` (8100–8199 на сервере свободны) |
| nginx | `/etc/nginx/sites-available/gpro.zeastudio.su` |
| Basic Auth | `/etc/nginx/geekspro2.htpasswd` |

## Предварительные условия

1. **DNS:** A-запись `gpro.zeastudio.su → 72.56.116.115` (зона на NS timeweb).
   На 20.10.2026 запись видна через 8.8.8.8, через 1.1.1.1 ещё нет — дождаться
   распространения перед выпуском сертификата. AAAA-записи не нужно.
2. **Репозиторий приватный**, на сервере — deploy key только на чтение.
3. **Память:** на сервере 3.8 ГБ, свободно ~1.4 ГБ. Лимиты контейнеров 2.0
   в сумме ~1.9 ГБ, реальное потребление ~0.6–0.9 ГБ. Следить за `free -h` после запуска;
   при нехватке — `GUNICORN_WORKERS=1`, `--concurrency 1` у celery или swap.

## Первый запуск

```bash
# 1. Код
mkdir -p /opt/geekspro2 && cd /opt/geekspro2
ssh-keygen -t ed25519 -f ~/.ssh/geekspro2_deploy -N ''     # публичный ключ → GitHub Deploy keys (read-only)
GIT_SSH_COMMAND='ssh -i ~/.ssh/geekspro2_deploy' \
  git clone git@github.com:Nurbolottop/GeeksProControl2.0.git .
git config core.sshCommand 'ssh -i ~/.ssh/geekspro2_deploy'

# 2. Окружение — только новые значения
cp .env.prod.example .env && chmod 600 .env
python3 -c "import secrets; print(secrets.token_urlsafe(50))"   # → SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # → POSTGRES_PASSWORD
# PROTECTED_VALUE_HASHES — хеши токена бота 1.0 и ID таблицы 1.0 (не сами значения):
for k in TELEGRAM_BOT_TOKEN RESERVE_SHEET_ID; do
  sed -n "s/^$k=//p" /root/GeeksProControl/.env | tr -d '"' | tr -d '\n' | sha256sum | cut -d' ' -f1
done

# 3. Хранилище (приложение в контейнере работает как uid 10001)
mkdir -p storage/static storage/media secrets backups
chown 10001:10001 storage/static storage/media
chmod 755 storage storage/static storage/media
chmod 700 secrets backups

# 4. Контейнеры (бот не стартует: он под профилем bot)
scripts/gp2-compose.sh --prod up -d --build
scripts/gp2-compose.sh --prod ps
scripts/gp2-compose.sh --prod exec web python manage.py check --deploy
scripts/gp2-compose.sh --prod exec web python manage.py createsuperuser
# Синтетические данные для тестирования (на пустой базе; данных 1.0 здесь нет)
scripts/gp2-compose.sh --prod exec web python manage.py seed_demo
curl -fsS -H 'Host: gpro.zeastudio.su' -H 'X-Forwarded-Proto: https' http://127.0.0.1:8100/healthz/
```

## nginx и HTTPS

```bash
# Basic Auth для тестового доступа (htpasswd на сервере нет — через openssl)
printf 'gpro:%s\n' "$(openssl passwd -apr1)" > /etc/nginx/geekspro2.htpasswd
chown root:www-data /etc/nginx/geekspro2.htpasswd && chmod 640 /etc/nginx/geekspro2.htpasswd

# Шаг 1: HTTP-конфиг для выпуска сертификата
mkdir -p /var/www/certbot
cp deploy/nginx/gpro.zeastudio.su.bootstrap.conf /etc/nginx/sites-available/gpro.zeastudio.su
ln -s /etc/nginx/sites-available/gpro.zeastudio.su /etc/nginx/sites-enabled/gpro.zeastudio.su
nginx -t && systemctl reload nginx
certbot certonly --webroot -w /var/www/certbot -d gpro.zeastudio.su

# Шаг 2: основной конфиг
cp deploy/nginx/gpro.zeastudio.su.conf /etc/nginx/sites-available/gpro.zeastudio.su
nginx -t && systemctl reload nginx
```

`certbot certonly --webroot` не редактирует конфиги nginx (в отличие от `--nginx`),
поэтому конфиг 1.0 гарантированно остаётся как есть. Продление — штатный таймер certbot.

Проверка: `https://gpro.zeastudio.su` спрашивает пароль; `/media/<путь>` без
входа в Control → редирект на `/login/`; `/protected-media/…` снаружи → 404.

## Обновление

```bash
cd /opt/geekspro2
git pull --ff-only
scripts/gp2-compose.sh --prod up -d --build      # web сам применит миграции
```

Откат кода: `git checkout <предыдущий коммит>` и тот же `up -d --build`.
Миграции, уже применённые к базе, откатываются только осознанно (`migrate <app> <номер>`).

## Резервные копии

```bash
# /etc/cron.d/geekspro2-backup
15 3 * * * root cd /opt/geekspro2 && docker compose -p geekspro2 -f docker-compose.prod.yml exec -T db \
  sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > backups/db-$(date +\%F).dump \
  && find backups -name 'db-*.dump' -mtime +14 -delete
```

Media — `rsync` каталога `storage/media` в то же место хранения.

## Telegram-бот на stage

Реальный бот 1.0 и рассылки стажёрам на stage запрещены. Бот запускается только
с собственным **тестовым** ботом 2.0 и только для своих тестовых аккаунтов:

```bash
# .env: TELEGRAM_BOT_TOKEN=<токен тестового бота 2.0>, TELEGRAM_OUTBOUND_MODE=allowlist,
#       TELEGRAM_TEST_CHAT_IDS=<свои chat_id>
scripts/gp2-compose.sh --prod --profile bot up -d bot
```

`TELEGRAM_OUTBOUND_MODE=on` при `GP2_ENVIRONMENT=stage` не даёт запуститься (gp2.E003),
токен бота 1.0 блокируется по хешу (gp2.E001).

Если до Telegram нет прямого маршрута, 1.0 использует SOCKS5-релей на хосте —
`TELEGRAM_PROXY_URL=socks5h://host.docker.internal:<порт>` (порт уточнить на сервере).

## Перенос данных 1.0

При деплое не выполняется: ни entrypoint, ни compose не читают данные 1.0.
Финальная миграция — [migration-plan.md](migration-plan.md), только по
отдельному разрешению руководителя.

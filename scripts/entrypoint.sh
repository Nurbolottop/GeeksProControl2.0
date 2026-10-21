#!/bin/sh
set -e

# Ожидание доступности базы данных.
# depends_on: service_healthy обычно уже гарантирует готовность,
# но проверка нужна при запуске без compose.
echo "Ожидание доступности базы данных ${POSTGRES_HOST}:${POSTGRES_PORT}..."
until nc -z -w 2 "$POSTGRES_HOST" "$POSTGRES_PORT"
do
  echo "Waiting for PostgreSQL database connection..."
  sleep 1
done

# Миграции и статику делает только web (RUN_MIGRATIONS=1): если их
# запускают сразу worker, beat и бот, они гоняются друг с другом.
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  # makemigrations здесь намеренно нет: миграции хранятся в репозитории
  # и создаются разработчиком, а не генерируются на сервере.
  echo "Применяем миграции..."
  python manage.py migrate --noinput

  echo "Собираем статические файлы..."
  python manage.py collectstatic --noinput

  # Администратор из env — только если пароль задан явно (разработка).
  # На сервере администратора создают вручную: manage.py createsuperuser.
  if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
    python manage.py createsuperuser --noinput 2>/dev/null || true
  fi
fi

# exec — чтобы SIGTERM от `docker stop` получил сам процесс (gunicorn,
# celery), а не эта оболочка, и остановка была штатной.
exec "$@"

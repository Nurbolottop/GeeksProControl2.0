#!/bin/sh
# Обёртка над docker compose для GeeksPro Control 2.0.
#
# Зачем: COMPOSE_PROJECT_NAME из .env сильнее, чем `name:` в compose-файле.
# Если в .env окажется значение 1.0 (geekspro), compose начнёт пересоздавать
# контейнеры 1.0. Здесь проект всегда задаётся явно (-p geekspro2), а .env
# проверяется до запуска.
#
#   scripts/gp2-compose.sh up -d                     # разработка
#   scripts/gp2-compose.sh --prod up -d --build      # сервер
set -eu

cd "$(dirname "$0")/.."

FILE=docker-compose.yml
if [ "${1:-}" = "--prod" ]; then
  FILE=docker-compose.prod.yml
  shift
fi

if [ ! -f .env ]; then
  echo "Нет .env — скопируйте .env.example (или .env.prod.example) и заполните." >&2
  exit 1
fi

project=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' .env | tr -d '"' | tail -1)
if [ -n "$project" ] && [ "$project" != "geekspro2" ]; then
  echo "В .env COMPOSE_PROJECT_NAME=$project — для 2.0 допустимо только geekspro2." >&2
  exit 1
fi

# Токен бота 1.0 или его таблица в .env 2.0 — не запускаемся.
hashes=$(sed -n 's/^PROTECTED_VALUE_HASHES=//p' .env | tr -d '" ' | tail -1)
for name in TELEGRAM_BOT_TOKEN RESERVE_SHEET_ID; do
  value=$(sed -n "s/^$name=//p" .env | tr -d '"' | tail -1)
  [ -z "$value" ] && continue
  hash=$(printf '%s' "$value" | (sha256sum 2>/dev/null || shasum -a 256) | cut -d' ' -f1)
  case ",$hashes," in
    *",$hash,"*)
      echo "$name в .env совпадает со значением 1.0 — запуск запрещён." >&2
      exit 1
      ;;
  esac
done

exec docker compose -p geekspro2 -f "$FILE" "$@"

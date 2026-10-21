#!/bin/sh
# Резервная копия Control 2.0 (этап 11A): БД (pg_dump -Fc), media, манифест sha256.
# Только база и файлы 2.0 — к 1.0 не обращается.
#
#   DC="scripts/gp2-compose.sh --prod" MEDIA_DIR=/opt/geekspro2/storage/media \
#     BACKUP_DIR=/opt/geekspro2/backups KEEP_DAYS=14 scripts/backup.sh
#   … --verify   — дополнительно восстановить дамп в одноразовую базу gp2_backup_check
set -eu
: "${DC:?Задайте DC — docker compose проекта geekspro2}"
: "${MEDIA_DIR:?Задайте MEDIA_DIR}"
BACKUP_DIR="${BACKUP_DIR:-/opt/geekspro2/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
stamp=$(date +%Y-%m-%d-%H%M)
dir="$BACKUP_DIR/$stamp"
umask 077
mkdir -p "$dir"
$DC exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "$dir/gp2.dump"
tar -C "$MEDIA_DIR" -czf "$dir/media.tar.gz" .
(cd "$MEDIA_DIR" && find . -type f -exec shasum -a 256 {} + | sort -k2) > "$dir/media.sha256"
shasum -a 256 "$dir/gp2.dump" "$dir/media.tar.gz" > "$dir/SHA256SUMS"
echo "Копия: $dir ($(du -sh "$dir" | cut -f1)), файлов media: $(wc -l < "$dir/media.sha256" | tr -d ' ')"
if [ "${1:-}" = "--verify" ]; then
  $DC exec -T db sh -c 'psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS gp2_backup_check" -c "CREATE DATABASE gp2_backup_check"' >/dev/null
  $DC exec -T db sh -c 'pg_restore --no-owner --role="$POSTGRES_USER" -U "$POSTGRES_USER" -d gp2_backup_check' < "$dir/gp2.dump"
  tables=$($DC exec -T db sh -c 'psql -U "$POSTGRES_USER" -d gp2_backup_check -tAc "SELECT count(*) FROM interns_intern"')
  $DC exec -T db sh -c 'psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE gp2_backup_check"' >/dev/null
  (cd "$dir" && shasum -a 256 -c SHA256SUMS >/dev/null) && echo "Проверка: дамп восстанавливается (стажёров: $tables), суммы совпадают"
fi
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} +

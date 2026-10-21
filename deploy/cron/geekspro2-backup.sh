#!/bin/sh
# /usr/local/sbin/geekspro2-backup — ежедневный бэкап Control 2.0 (staging), запускает cron.
# Только база и media 2.0 (scripts/backup.sh), к 1.0 не обращается.
#  * один запуск одновременно (flock);
#  * перед копией — контроль свободного места (MIN_FREE_MB);
#  * каждый день — проверка sha256 созданной копии; по воскресеньям — восстановление
#    дампа в одноразовую базу (scripts/backup.sh --verify);
#  * хранение KEEP_DAYS дней, каталог и файлы — только root (umask 077);
#  * итог — в backups/last-status и backups/backup.log; при сбое — уведомление в Control.
set -u
ROOT=/opt/geekspro2
BACKUP_DIR="$ROOT/backups"
LOG="$BACKUP_DIR/backup.log"
MIN_FREE_MB="${MIN_FREE_MB:-3072}"
KEEP_DAYS="${KEEP_DAYS:-14}"
umask 077
mkdir -p "$BACKUP_DIR"
cd "$ROOT" || exit 1
exec 9>/run/geekspro2-backup.lock
flock -n 9 || { echo "$(date -Is) уже выполняется — пропуск" >> "$LOG"; exit 0; }

status() { printf '%s %s\n' "$(date -Is)" "$1" > "$BACKUP_DIR/last-status"; echo "$(date -Is) $1" >> "$LOG"; }
fail() {
  status "FAIL: $1"
  scripts/gp2-compose.sh --prod exec -T web python manage.py shell -c "
from apps.notifications.services import notify
from apps.notifications.models import NotificationLevel
notify('Бэкап staging не выполнен', level=NotificationLevel.CRITICAL, description='$1', dedup_key='gp2-backup-fail')
" >/dev/null 2>&1 || true
  exit 1
}

free=$(df -Pm "$BACKUP_DIR" | awk 'NR==2 {print $4}')
[ "$free" -ge "$MIN_FREE_MB" ] || fail "мало места: ${free} МБ свободно, нужно ≥ ${MIN_FREE_MB} МБ"
VERIFY=""
[ "$(date +%u)" = "7" ] && VERIFY="--verify"
DC="scripts/gp2-compose.sh --prod" MEDIA_DIR="$ROOT/storage/media" BACKUP_DIR="$BACKUP_DIR" \
  KEEP_DAYS="$KEEP_DAYS" sh scripts/backup.sh $VERIFY >> "$LOG" 2>&1 || fail "scripts/backup.sh завершился с ошибкой"
latest=$(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20*' | sort | tail -1)
[ -n "$latest" ] || fail "копия не создана"
(cd "$latest" && sha256sum -c SHA256SUMS >/dev/null 2>&1) || fail "контрольные суммы копии $latest не совпали"
[ -s "$latest/gp2.dump" ] || fail "пустой дамп в $latest"
status "OK $(basename "$latest") $(du -sh "$latest" | cut -f1)${VERIFY:+ + проверка восстановлением}"

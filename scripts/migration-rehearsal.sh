#!/bin/sh
# Репетиция переноса 1.0 → 2.0 (этап 11A, docs/migration-plan.md §2).
#
#   DC=<обёртка docker compose отдельного проекта> scripts/migration-rehearsal.sh --synthetic OUT_DIR
#   DC=<…> scripts/migration-rehearsal.sh --dump /root/backups/gp1-rehearsal.dump OUT_DIR   # только с разрешения
#
# DC — команда docker compose ИЗОЛИРОВАННОГО проекта репетиции (своя БД, без бота,
# TELEGRAM_OUTBOUND_MODE=off), например:
#   DC="docker compose -p geekspro2-rehearsal -f docker-compose.prod.yml"
# Пересоздаётся только база gp2_rehearsal (и gp1_synth в режиме --synthetic).
# Ничего не исправляется автоматически: команды сверки — только отчёт.
set -eu
MODE="$1"; shift
if [ "$MODE" = "--dump" ]; then DUMP="$1"; shift; fi
OUT="$1"
: "${DC:?Задайте DC — docker compose изолированного проекта репетиции}"
mkdir -p "$OUT"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$OUT/rehearsal.log"; }
psql_db() { $DC exec -T db sh -c "psql -v ON_ERROR_STOP=1 -U \"\$POSTGRES_USER\" -d postgres -c \"$1\"" >/dev/null; }
# </dev/null: docker compose run иначе съедает stdin (например, строки файла целей в цикле)
manage() { db="$1"; shift; $DC run --rm --no-deps -T -e RUN_MIGRATIONS=0 -e POSTGRES_DB="$db" web python manage.py "$@" </dev/null; }

if [ "$MODE" = "--synthetic" ]; then
  log "Синтетическая база 1.0: gp1_synth (схема ровно до миграций 1.0)"
  psql_db "DROP DATABASE IF EXISTS gp1_synth"
  psql_db "CREATE DATABASE gp1_synth"
  : "${GP1_TARGETS:?Задайте GP1_TARGETS — файл «app миграция» последних миграций 1.0}"
  while read -r app target; do
    manage gp1_synth migrate "$app" "$target" --noinput > /dev/null || { log "ОШИБКА migrate $app $target"; exit 1; }
  done < "$GP1_TARGETS"
  applied=$(manage gp1_synth showmigrations --plan | grep -c '\[X\]')
  log "схема 1.0: применено миграций $applied"

  manage gp1_synth seed_legacy_synthetic > "$OUT/seed.log" 2>&1 || { log "ОШИБКА генерации синтетики — см. seed.log"; exit 1; }
  grep 'Синтетика' "$OUT/seed.log" | tee -a "$OUT/rehearsal.log"
  $DC exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc gp1_synth' > "$OUT/gp1-synthetic.dump"
  DUMP="$OUT/gp1-synthetic.dump"
fi

log "Дамп: $(basename "$DUMP"), sha256 $(shasum -a 256 "$DUMP" | cut -c1-16)…"
psql_db "DROP DATABASE IF EXISTS gp2_rehearsal"
psql_db "CREATE DATABASE gp2_rehearsal"
START=$(date +%s)
$DC exec -T db sh -c 'pg_restore --no-owner --role="$POSTGRES_USER" -U "$POSTGRES_USER" -d gp2_rehearsal' < "$DUMP"
log "pg_restore: $(( $(date +%s) - START )) с"

json() { sed -n '/^{/,$p'; }   # entrypoint печатает служебные строки до JSON
manage gp2_rehearsal migration_report --media > "$OUT/report-before.raw" || { log "ОШИБКА отчёта до migrate"; exit 1; }
json < "$OUT/report-before.raw" > "$OUT/report-before.json"
python3 -c "import json,sys; r=json.load(open(sys.argv[1])); assert r['counts'].get('interns'), 'пустая база'" \
  "$OUT/report-before.json" || { log "ОШИБКА: в восстановленной базе нет стажёров — дамп пуст?"; exit 1; }
START=$(date +%s)
manage gp2_rehearsal migrate --noinput > "$OUT/migrate.log"
log "migrate кодом 2.0: $(( $(date +%s) - START )) с, применено $(grep -c ' OK' "$OUT/migrate.log")"
manage gp2_rehearsal migration_report --media > "$OUT/report-after.raw" || { log "ОШИБКА отчёта после migrate"; exit 1; }
json < "$OUT/report-after.raw" > "$OUT/report-after.json"
# --user 0: отчёты лежат с правами 600 (персональные данные), каталог смонтирован только для чтения
if $DC run --rm --no-deps -T --user 0 -e RUN_MIGRATIONS=0 -e POSTGRES_DB=gp2_rehearsal -v "$(cd "$OUT" && pwd):/rehearsal:ro" \
    web python manage.py migration_report --compare /rehearsal/report-before.json /rehearsal/report-after.json \
    > "$OUT/compare.txt" 2>&1; then
  log "Сверка до/после: без расхождений"
else
  log "Сверка до/после: РАСХОЖДЕНИЯ — см. compare.txt (переключение запрещено)"
fi

for cmd in internship_reconciliation graduate_reconciliation membership_rule_audit freeze_reconciliation; do
  manage gp2_rehearsal "$cmd" > "$OUT/$cmd.txt" 2>&1 || true
  log "$cmd: $(wc -l < "$OUT/$cmd.txt" | tr -d ' ') строк отчёта"
done
manage gp2_rehearsal internship_backfill > "$OUT/internship_backfill-dry.txt" 2>&1 || true
log "internship_backfill (без --apply): $(wc -l < "$OUT/internship_backfill-dry.txt" | tr -d ' ') строк"
log "Итог: report-before.json / report-after.json / *.txt в $OUT"

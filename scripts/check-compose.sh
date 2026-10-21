#!/bin/sh
# Проверка Docker Compose на хосте (этап 11A): синтаксис, сервисы, переменные,
# профили, тома, сети, зависимости, один polling, dev-зависимости, лимиты.
# Не запускает контейнеры и не обращается к сети — только `docker compose config`.
#
#   scripts/check-compose.sh            # dev + prod с шаблонами окружения
set -eu
cd "$(dirname "$0")/.."
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
# Шаблоны окружения, а не настоящие .env: проверяем конфигурацию, не секреты
cp .env.example "$tmp/dev.env"
cp .env.prod.example "$tmp/prod.env"
docker compose -f docker-compose.yml --env-file "$tmp/dev.env" --profile bot config --format json > "$tmp/dev.json"
docker compose -f docker-compose.prod.yml --env-file "$tmp/prod.env" --profile bot config --format json > "$tmp/prod.json"
python3 - "$tmp" <<'PY'
import json, re, sys
tmp = sys.argv[1]
dev, prod = (json.load(open(f'{tmp}/{n}.json')) for n in ('dev', 'prod'))
env = dict(l.split('=', 1) for l in open(f'{tmp}/prod.env').read().splitlines() if '=' in l and not l.startswith('#'))
errors = []
def check(ok, text):
    if not ok:
        errors.append(text)
def mb(value):
    value = str(value).lower()
    n = float(re.sub(r'[a-z]', '', value) or 0)
    return n / 1048576 if value.isdigit() else n * (1024 if value.endswith('g') else 1)
for label, cfg in (('dev', dev), ('prod', prod)):
    check(cfg.get('name') == 'geekspro2', f'{label}: имя проекта не geekspro2')
    for kind in ('volumes', 'networks'):
        for key, item in (cfg.get(kind) or {}).items():
            check(not item.get('external'), f'{label}: {kind} {key} external')
            check(str(item.get('name', '')).startswith('geekspro2_'), f'{label}: {kind} {key} без префикса geekspro2_')
    pollers = [n for n, s in cfg['services'].items()
               if re.search(r'run_(telegram|graduate)_bot', ' '.join(s.get('command') or []))]
    check(pollers == ['bot'], f'{label}: polling-процессы {pollers} (нужен ровно один — bot)')
    check(cfg['services']['bot'].get('profiles') == ['bot'], f'{label}: бот не только в профиле bot')
    check('run_graduate_bot' not in json.dumps(cfg), f'{label}: в конфигурации есть run_graduate_bot')
    for name, svc in cfg['services'].items():
        if name in ('db', 'redis'):
            continue
        deps = svc.get('depends_on') or {}
        check(all(deps.get(d, {}).get('condition') == 'service_healthy' for d in ('db', 'redis')),
              f'{label}: {name} не ждёт healthy db/redis')
        check(svc.get('container_name') is None, f'{label}: {name} задаёт container_name')
check(dev['services']['web']['build'].get('args', {}).get('INSTALL_DEV') == '1', 'dev: нет INSTALL_DEV=1')
for name, svc in prod['services'].items():
    args = (svc.get('build') or {}).get('args') or {}
    check(args.get('INSTALL_DEV', '0') in ('0', None), f'prod: {name} собирается с dev-зависимостями')
    if name in ('db', 'redis'):
        check(not svc.get('ports'), f'prod: {name} публикует порты')
    for port in svc.get('ports') or []:
        check(port.get('host_ip') == '127.0.0.1', f'prod: {name} слушает не только 127.0.0.1')
    check(svc.get('mem_limit') is not None, f'prod: {name} без mem_limit')
total = sum(mb(s.get('mem_limit', 0)) for s in prod['services'].values())
check(total <= 1450, f'prod: сумма лимитов {total:.0f} МБ > 1450 МБ (свободно на сервере ~1.4 ГБ)')
check(env.get('TELEGRAM_OUTBOUND_MODE') == 'off', 'шаблон stage: TELEGRAM_OUTBOUND_MODE не off')
check(env.get('GP2_ENVIRONMENT') == 'stage', 'шаблон stage: GP2_ENVIRONMENT не stage')
check(not env.get('TELEGRAM_BOT_TOKEN'), 'шаблон stage: задан TELEGRAM_BOT_TOKEN')
check(not any(re.search(r'\d{8,10}:AA[\w-]{30,}', v) for v in env.values()), 'шаблон stage: похоже на токен бота')
print(f'Сервисов prod: {len(prod["services"])}, лимиты памяти: {total:.0f} МБ')
if errors:
    print('\n'.join('ОШИБКА: ' + e for e in errors)); sys.exit(1)
print('Compose: проверки пройдены')
PY

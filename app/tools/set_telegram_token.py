#!/usr/bin/env python3
"""Безопасно записать токен Telegram-бота GeeksPro 2.0 в локальный .env.

Запуск из корня проекта:

    python3 app/tools/set_telegram_token.py

* токен вводится вставкой, символы на экране НЕ показываются (getpass);
* в аргументы командной строки, историю терминала и логи он не попадает;
* проверяются формат и то, что это не токен бота GeeksPro 1.0 (по хешу);
* запросов в Telegram скрипт не делает;
* .env остаётся только у владельца (права 600) и игнорируется Git.

Только стандартная библиотека Python — Django и Docker не нужны.
"""
import getpass
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / '.env'
TOKEN_RE = re.compile(r'^\d{5,15}:[A-Za-z0-9_-]{30,64}$')


def token_format_ok(token: str) -> bool:
    return bool(TOKEN_RE.match(token))


def read_env(path: Path) -> list[str]:
    return path.read_text(encoding='utf-8').splitlines() if path.exists() else []


def env_value(lines: list[str], key: str) -> str:
    for line in lines:
        if line.startswith(f'{key}='):
            return line.split('=', 1)[1].strip().strip('"')
    return ''


def protected_hashes(lines: list[str]) -> set[str]:
    raw = env_value(lines, 'PROTECTED_VALUE_HASHES')
    return {h.strip().lower() for h in raw.split(',') if h.strip()}


def is_protected(token: str, lines: list[str]) -> bool:
    return hashlib.sha256(token.encode()).hexdigest() in protected_hashes(lines)


def with_value(lines: list[str], key: str, value: str) -> list[str]:
    """Заменить KEY=… (или добавить в конец), остальные строки — как были."""
    result, done = [], False
    for line in lines:
        if line.startswith(f'{key}='):
            if not done:
                result.append(f'{key}={value}')
                done = True
            continue
        result.append(line)
    if not done:
        result.append(f'{key}={value}')
    return result


def write_env(path: Path, lines: list[str]) -> None:
    """Атомарная запись с правами 600 — без промежуточного файла с другими правами."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.env.', text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(lines) + '\n')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    os.chmod(path, 0o600)


def env_is_git_ignored() -> bool:
    try:
        return subprocess.run(['git', 'check-ignore', '-q', str(ENV_FILE)], cwd=ROOT).returncode == 0
    except FileNotFoundError:
        return True  # git нет — и коммитить некуда


def main() -> int:
    if not ENV_FILE.exists():
        print('Нет файла .env в корне проекта — сначала создайте его из .env.example.')
        return 1
    if not env_is_git_ignored():
        print('Стоп: .env не игнорируется Git — токен мог бы попасть в репозиторий.')
        return 1
    if not sys.stdin.isatty():
        print('Запустите скрипт в обычном терминале: нужен скрытый ввод.')
        return 1
    print('Вставьте НОВЫЙ токен @GeeksProAutoBot (после /revoke в @BotFather) и нажмите Enter.')
    print('Символы не отображаются — это нормально.')
    token = getpass.getpass('Токен: ').strip()
    if not token_format_ok(token):
        print('Формат не похож на токен бота (ожидается вида 123456789:AA…). Ничего не записано.')
        return 1
    lines = read_env(ENV_FILE)
    if is_protected(token, lines):
        print('Это токен бота GeeksPro 1.0 — в 2.0 он запрещён. Ничего не записано.')
        return 1
    write_env(ENV_FILE, with_value(lines, 'TELEGRAM_BOT_TOKEN', token))
    bot_id = token.split(':', 1)[0]
    print(f'Готово: токен сохранён в .env (бот id {bot_id[:3]}…, права файла 600).')
    print('Исходящие сообщения по-прежнему выключены (TELEGRAM_OUTBOUND_MODE=off).')
    return 0


if __name__ == '__main__':
    sys.exit(main())

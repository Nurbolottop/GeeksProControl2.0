#!/usr/bin/env python3
"""Дымовая проверка развёрнутого Control 2.0 (этап 11A): локальная репетиция и staging.

    GP2_SMOKE_BASE=https://gpro.zeastudio.su \
    GP2_SMOKE_USERS='head=логин:пароль,pm=логин:пароль,lead=логин:пароль' \
    GP2_SMOKE_MEDIA=documents/…/file.pdf \
    python3 scripts/smoke_check.py

Учётные записи — только тестовые (синтетические); значения в вывод не попадают.
Basic Auth staging — GP2_SMOKE_BASIC='логин:пароль'. Без сторонних библиотек.
"""
import base64
import http.cookiejar
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get('GP2_SMOKE_BASE', 'http://127.0.0.1:8110').rstrip('/')
HOST = os.environ.get('GP2_SMOKE_HOST', '')          # Host при обращении к gunicorn напрямую
FORWARDED_HTTPS = os.environ.get('GP2_SMOKE_FORWARDED', '1') == '1'  # добавить X-Forwarded-Proto самим
# Приложение видит https (за nginx или напрямую с X-Forwarded-Proto): Referer для CSRF — https
SECURE = os.environ.get('GP2_SMOKE_SECURE', '1') == '1'
PAGES = {
    'head': ['/', '/projects/', '/interns/', '/interns/graduates/', '/tasks/', '/weekly-reports/', '/video/',
             '/reserve/board/', '/telegram/', '/teams/leads/'],
    'pm': ['/pm/', '/pm/reports/', '/pm/meetings/'],
    'lead': ['/lead/', '/lead/reports/', '/lead/meetings/', '/lead/telegram/'],
}
FORBIDDEN = {'pm': ['/projects/', '/telegram/'], 'lead': ['/interns/', '/video/']}
results = []


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def opener():
    jar = http.cookiejar.CookieJar()
    handlers = [urllib.request.HTTPCookieProcessor(jar), NoRedirect()]
    return urllib.request.build_opener(*handlers), jar


def request(op, path, data=None, headers=None):
    headers = dict(headers or {})
    if HOST:
        headers['Host'] = HOST
    if FORWARDED_HTTPS:
        headers['X-Forwarded-Proto'] = 'https'
    basic = os.environ.get('GP2_SMOKE_BASIC')
    if basic:
        headers['Authorization'] = 'Basic ' + base64.b64encode(basic.encode()).decode()
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=body, headers=headers)
    try:
        response = op.open(req, timeout=20)
        return response.status, dict(response.headers), response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read().decode('utf-8', 'replace')


def check(name, ok, detail=''):
    results.append((name, ok, detail))
    print(f'{"OK  " if ok else "FAIL"} {name}{" — " + detail if detail and not ok else ""}')


def cookie_header(jar):
    # Secure-cookie по http (локальная репетиция за X-Forwarded-Proto): отправляем вручную
    return '; '.join(f'{c.name}={c.value}' for c in jar)


def login(role, credentials):
    op, jar = opener()
    status, _h, html = request(op, '/login/')
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    username, password = credentials.split(':', 1)
    referer = re.sub(r'^http://', 'https://', BASE) if SECURE else BASE
    headers = {'Cookie': cookie_header(jar), 'Referer': referer + '/login/'}
    status, response_headers, _ = request(op, '/login/', {'username': username, 'password': password,
                                                         'csrfmiddlewaretoken': token.group(1) if token else ''},
                                          headers)
    check(f'вход {role}', status == 302 and '/login' not in response_headers.get('Location', ''),
          f'код {status}')
    return op, jar


def get(op, jar, path):
    return request(op, path, headers={'Cookie': cookie_header(jar)})


def main():
    op, jar = opener()
    status, _h, body = request(op, '/healthz/')
    check('healthz', status == 200 and body.strip() == 'ok', f'код {status}')
    status, _h, body = request(op, '/readyz/')
    check('readyz (БД и Redis)', status == 200 and '"fail"' not in body, f'{status} {body[:80]}')
    status, _h, body = request(op, '/static/gp2/components.css')
    if os.environ.get('GP2_SMOKE_STATIC', '1') == '1':
        check('статика через nginx', status == 200 and 'gp-' in body, f'код {status}')
    media = os.environ.get('GP2_SMOKE_MEDIA')
    if media:
        status, headers, _ = request(op, f'/media/{media}')
        check('media: аноним → вход', status == 302 and '/login' in headers.get('Location', ''), f'код {status}')
    users = dict(item.split('=', 1) for item in os.environ.get('GP2_SMOKE_USERS', '').split(',') if '=' in item)
    for role, credentials in users.items():
        op, jar = login(role, credentials)
        for path in PAGES.get(role, []):
            status, headers, html = get(op, jar, path)
            check(f'{role} {path}', status == 200 and 'Traceback' not in html,
                  f'код {status} {headers.get("Location", "")}')
        for path in FORBIDDEN.get(role, []):
            status, _h, _ = get(op, jar, path)
            check(f'{role} не видит {path}', status in (302, 403, 404), f'код {status}')
        if media:
            status, headers, body = get(op, jar, f'/media/{media}')
            if role == 'head':
                accel = headers.get('X-Accel-Redirect') or (status == 200 and body)
                check('media: руководитель получает файл', status == 200 and bool(accel), f'код {status}')
            else:
                check(f'media: {role} без прав → 404', status == 404, f'код {status}')
    failed = [r for r in results if not r[1]]
    print(f'\nПроверок: {len(results)}, ошибок: {len(failed)}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())

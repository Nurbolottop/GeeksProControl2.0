"""Обход страниц на копии реальных данных (этап 11C): ошибки 500, которых нет на синтетике.

    docker compose … run --rm --no-deps -T web python manage.py shell < scripts/crawl_check.py

Под руководителем, ПМ и тимлидом (force_login, без паролей) открывает внутренние
ссылки только методом GET, до GP2_CRAWL_LIMIT страниц на роль. Только вне production:
GET-страницы могут отмечать прочитанное. В вывод — шаблоны путей (числа → <id>) и
тип ошибки, без данных людей.
"""
import os
import re
from collections import deque
from urllib.parse import urldefrag, urlparse

from django.conf import settings
from django.test import Client
from django.test.utils import override_settings

from apps.accounts.models import User
from apps.teams.models import TeamMember, TeamRole

if settings.GP2_ENVIRONMENT == 'production':
    raise SystemExit('Обход — только на копии, не в production.')
LIMIT = int(os.environ.get('GP2_CRAWL_LIMIT', '700'))
HOST = (settings.ALLOWED_HOSTS or ['localhost'])[0].lstrip('.')
SKIP = re.compile(r'^/(logout|admin|media|static|ui|healthz|readyz)|/(delete|archive|export|download)/?|\.(csv|xlsx|pdf)$')
HREF = re.compile(r'href="([^"#][^"]*)"')
ACTIVE = TeamMember.Status.ACTIVE


def pattern(path):
    return re.sub(r'\d+', '<id>', path)


def crawl(role, user, start):
    client = Client(HTTP_HOST=HOST, secure=True)
    client.force_login(user)
    seen, queue, errors, opened, by_pattern = set(), deque(start), {}, 0, {}
    while queue and opened < LIMIT:
        url = urldefrag(queue.popleft())[0]
        parsed = urlparse(url)
        if parsed.netloc and parsed.netloc != HOST:
            continue
        path = parsed.path + (f'?{parsed.query}' if parsed.query else '')
        if not parsed.path.startswith('/') or SKIP.search(parsed.path) or path in seen:
            continue
        key = pattern(path)
        if by_pattern.get(key, 0) >= 4:  # одинаковые страницы разных записей — не больше 4
            continue
        seen.add(path)
        by_pattern[key] = by_pattern.get(key, 0) + 1
        opened += 1
        try:
            response = client.get(path)
        except Exception as error:  # исключение представления/шаблона
            errors.setdefault(key, f'{type(error).__name__}: {str(error)[:140]}')
            continue
        if response.status_code >= 500:
            errors.setdefault(key, f'код {response.status_code}')
        elif response.status_code == 200 and 'html' in response.get('Content-Type', ''):
            for href in HREF.findall(response.content.decode('utf-8', 'replace')):
                queue.append(href.replace('&amp;', '&'))
    print(f'{role}: открыто страниц {opened}, шаблонов {len(by_pattern)}, ошибок {len(errors)}')
    for key, detail in sorted(errors.items()):
        print(f'  FAIL {role} {key} — {detail}')
    return len(errors)


member = lambda role, user_role: TeamMember.objects.filter(  # noqa: E731
    status=ACTIVE, role=role, intern__user__role=user_role, intern__user__is_active=True).select_related(
    'intern__user').order_by('pk').first()
total = 0
with override_settings(ALLOWED_HOSTS=[HOST, 'testserver']):
    head = User.objects.filter(role='head', is_active=True).order_by('pk').first()
    if head:
        total += crawl('head', head, ['/'])
    pm = member(TeamRole.PROJECT_MANAGER, 'pm')
    if pm:
        total += crawl('pm', pm.intern.user, ['/pm/'])
    lead = member(TeamRole.TEAM_LEAD, 'team_lead')
    if lead:
        total += crawl('lead', lead.intern.user, ['/lead/'])
print(f'\nИтого ошибок: {total}')

"""Проверка ролей и основных функций на реальных данных (этап 11C) — без паролей.

    docker compose … run --rm --no-deps -T -v "$PWD/scripts:/checks:ro" web \
        python manage.py shell < scripts/role_check.py            # только чтение
    … -e GP2_CHECK_WRITE=1 …                                       # + запись (только НЕ production)

Входит через force_login реальными учётками руководителя, ПМ и тимлида
(по одной, выбираются по данным), открывает страницы, проверяет границы
доступа и файлы. В вывод — только коды ответов и количества, без ФИО.
Запись (задача, статус, комментарий, отчёт, оценка) — только на копии:
в production скрипт отказывается писать.
"""
import os
import random

from django.conf import settings
from django.test import Client
from django.test.utils import override_settings

from apps.accounts.models import User
from apps.documents.models import Document
from apps.interns.models import Intern
from apps.projects.models import Project
from apps.teams.models import TeamMember, TeamRole

from django.contrib.auth import user_logged_in
from django.contrib.auth.models import update_last_login

# Проверка не должна менять учётки: force_login иначе обновит last_login реальных людей
user_logged_in.disconnect(update_last_login, dispatch_uid='update_last_login')
WRITE = os.environ.get('GP2_CHECK_WRITE') == '1'
if WRITE and settings.GP2_ENVIRONMENT == 'production':
    raise SystemExit('GP2_CHECK_WRITE запрещён в production.')
HOST = (settings.ALLOWED_HOSTS or ['localhost'])[0].lstrip('.')
results = []


def check(name, ok, detail=''):
    results.append(ok)
    print(f'{"OK  " if ok else "FAIL"} {name}{" — " + str(detail) if detail and not ok else ""}')


def client_for(user):
    client = Client(HTTP_HOST=HOST, secure=True)
    client.force_login(user)
    return client


def page(client, role, path, expect=(200,)):
    try:
        response = client.get(path)
        ok = response.status_code in expect and b'Traceback' not in response.content
        check(f'{role} {path}', ok, f'код {response.status_code}')
        return response
    except Exception as error:  # ошибка шаблона/представления — не прячем
        check(f'{role} {path}', False, f'{type(error).__name__}: {str(error)[:120]}')


ACTIVE = TeamMember.Status.ACTIVE
memberships = TeamMember.objects.filter(status=ACTIVE, intern__user__isnull=False, intern__user__is_active=True)
head = User.objects.filter(role='head', is_active=True).order_by('pk').first()
pm_member = memberships.filter(role=TeamRole.PROJECT_MANAGER, intern__user__role='pm').select_related(
    'intern__user', 'project').order_by('pk').first()
lead_member = memberships.filter(role=TeamRole.TEAM_LEAD, intern__user__role='team_lead').select_related(
    'intern__user', 'project').order_by('pk').first()
print(f'Учётки: руководитель {"есть" if head else "НЕТ"}, ПМ с проектом {"есть" if pm_member else "НЕТ"}, '
      f'тимлид с проектом {"есть" if lead_member else "НЕТ"}')

with override_settings(ALLOWED_HOSTS=[HOST, 'testserver']):
    anon = Client(HTTP_HOST=HOST, secure=True)
    check('аноним → вход', anon.get('/').status_code == 302)
    if head:
        c = client_for(head)
        for path in ('/', '/projects/', '/interns/', '/interns/graduates/', '/tasks/', '/weekly-reports/',
                     '/reserve/board/', '/telegram/', '/teams/leads/', '/clients/', '/documents/', '/video/'):
            page(c, 'head', path)
        rnd = random.Random(11)
        projects = list(Project.objects.values_list('pk', flat=True))
        for pk in rnd.sample(projects, min(10, len(projects))):
            page(c, 'head', f'/projects/{pk}/')
            page(c, 'head', f'/projects/{pk}/?tab=tasks')
        interns = list(Intern.objects.values_list('pk', flat=True))
        for pk in rnd.sample(interns, min(10, len(interns))):
            page(c, 'head', f'/interns/{pk}/')
        docs = [d for d in Document.objects.exclude(file='').order_by('?')[:10]]
        ok_files = 0
        for d in docs:
            r = c.get(f'/media/{d.file.name}')
            ok_files += r.status_code == 200
            if anon.get(f'/media/{d.file.name}').status_code != 302:
                check('media: аноним не получает файл', False, 'код не 302')
        check(f'media: руководитель открывает документы ({ok_files}/{len(docs)})', ok_files == len(docs))
    for role, member, pages, forbidden in (
        ('pm', pm_member, ('/pm/', '/pm/reports/', '/pm/meetings/'), ('/projects/', '/telegram/', '/interns/')),
        ('lead', lead_member, ('/lead/', '/lead/reports/', '/lead/meetings/', '/lead/telegram/'),
         ('/interns/', '/video/', '/projects/')),
    ):
        if member is None:
            continue
        c = client_for(member.intern.user)
        for path in pages:
            page(c, role, path)
        page(c, role, f'/{role}/{member.project_id}/')
        page(c, role, f'/{role}/{member.project_id}/?tab=tasks')
        for path in forbidden:
            r = c.get(path)
            check(f'{role} не видит {path}', r.status_code in (302, 403, 404), f'код {r.status_code}')
        # «Чужой» — где человек не состоял никогда: прошлые проекты видны ему только для чтения
        ever = set(TeamMember.objects.filter(intern=member.intern).values_list('project_id', flat=True))
        other = Project.objects.exclude(pk__in=ever).order_by('pk').first()
        if other:
            r = c.get(f'/{role}/{other.pk}/')
            check(f'{role}: чужой проект → 404', r.status_code == 404, f'код {r.status_code}')
            doc = Document.objects.filter(project=other).exclude(file='').first()
            if doc:
                r = c.get(f'/media/{doc.file.name}')
                check(f'{role}: документ чужого проекта → 404', r.status_code == 404, f'код {r.status_code}')

if WRITE and lead_member:
    from apps.reports import weekly
    from apps.tasks import workflow

    user, project = lead_member.intern.user, lead_member.project
    try:
        directions = weekly.lead_directions(lead_member.intern, project)
        task = workflow.create_task(project, user, title='Проверка переноса (копия)',
                                    direction=directions[0] if directions else '')
        check('lead: создать задачу', task.pk is not None)
        workflow.update_task(task, user, title='Проверка переноса (копия, изменено)', description='',
                             priority=task.priority, deadline=None)
        check('lead: изменить задачу', True)
        workflow.add_comment(task, user, 'комментарий проверки')
        check('lead: комментарий', True)
    except Exception as error:
        check('lead: задачи', False, f'{type(error).__name__}: {str(error)[:160]}')
    try:
        weekly.submit_lead_report(user, project, 'Отчёт проверки переноса (копия)')
        check('lead: сдать еженедельный отчёт', True)
    except Exception as error:
        check('lead: сдать еженедельный отчёт', False, f'{type(error).__name__}: {str(error)[:160]}')
    target = TeamMember.objects.filter(project=project, status=ACTIVE, role__in=directions).exclude(
        intern=lead_member.intern).first()
    if target:
        try:
            weekly.evaluate(user, target.intern, project, 8, 'оценка проверки')
            check('lead: поставить оценку', True)
        except Exception as error:
            check('lead: поставить оценку', False, f'{type(error).__name__}: {str(error)[:160]}')
if WRITE and pm_member:
    from apps.reports import weekly

    try:
        weekly.submit_pm_report(pm_member.intern.user, 'Сводный отчёт проверки (копия)',
                                project_ids=[pm_member.project_id])
        check('pm: сдать сводный отчёт', True)
    except Exception as error:
        check('pm: сдать сводный отчёт', False, f'{type(error).__name__}: {str(error)[:160]}')

print(f'\nПроверок: {len(results)}, ошибок: {results.count(False)}')

"""Раздача загруженных файлов (media) только тем, кому их можно видеть.

В media лежат договоры, ТЗ, резюме, вложения задач — по прямой ссылке их
открывать нельзя. Поэтому /media/… идёт через Django и проверяет право на
конкретный файл (can_open):

  * публичное (PUBLIC_MEDIA_PREFIXES — фото на витрине кандидата) — всем;
  * руководитель, администратор, superuser — любой файл;
  * ПМ и тимлид — материалы и скриншоты задач своих проектов (этап 10A);
  * ПМ и тимлид — документы (все версии) и файл брифа проектов, к
    документам которых у них есть доступ (project_documents.rights:
    назначенный ПМ/тимлид; тимлид — и сданные проекты, которые вёл);
    тимлид — ещё файлы своей карточки в резерве;
  * остальные — ничего.

Аноним без права уходит на вход, вошедший без права получает 404 — чтобы
по ответу нельзя было понять, есть ли такой файл. В production сам файл
отдаёт nginx по X-Accel-Redirect из internal-location, Django его не читает.
"""
import mimetypes
import posixpath
from pathlib import Path

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import FileResponse, Http404, HttpResponse


def _safe_path(path: str) -> str:
    """Нормализованный относительный путь без выхода за MEDIA_ROOT."""
    normalized = posixpath.normpath(path).lstrip('/')
    if normalized in ('', '.') or normalized.startswith('..'):
        raise Http404
    return normalized


def can_open(user, path: str) -> bool:
    """Право пользователя на файл media по его пути (имени в FileField)."""
    from django.db.models import Q

    from apps.accounts import access
    from apps.accounts.models import User
    from apps.projects.models import Project

    if path.startswith(tuple(settings.PUBLIC_MEDIA_PREFIXES)):
        return True
    if not user.is_authenticated:
        return False
    if access.has_full_access(user):
        return True
    if user.role in (User.Role.PROJECT_MANAGER, User.Role.TEAM_LEAD):
        from apps.documents.models import ProjectBrief
        from apps.documents.project_documents import rights

        # Документы проекта (и все их версии) и файл брифа — по тем же
        # правам, что вкладка «Документы» (apps.documents.project_documents)
        projects = Project.objects.filter(
            Q(documents__file=path) | Q(pk__in=ProjectBrief.objects.filter(requirements_file=path).values('project_id')),
        ).distinct()
        if any(rights(user, project).view for project in projects):
            return True
        # Задачи (этап 10A): материалы и скриншоты сдач — тем, кто ведёт задачи
        # этого проекта (активный ПМ / тимлид, apps.tasks.workflow.actor)
        from apps.tasks.workflow import actor

        task_projects = Project.objects.filter(
            Q(tasks__attachments__file=path) | Q(tasks__submissions__screenshot=path)).distinct()
        if path.startswith('tasks/') and any(actor(user, project) is not None for project in task_projects):
            return True
    if user.role == User.Role.TEAM_LEAD:
        from apps.reserve.services import reserve_card_of

        card = reserve_card_of(user)
        return bool(card and path in (card.photo.name, card.resume_file.name))
    return False


def protected_media(request, path):
    path = _safe_path(path)
    if not can_open(request.user, path):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        raise Http404

    prefix = settings.MEDIA_ACCEL_REDIRECT_PREFIX
    if prefix:
        response = HttpResponse()
        response['X-Accel-Redirect'] = prefix.rstrip('/') + '/' + path
        # Тип файла определяет nginx; пустой Content-Type не мешает.
        del response['Content-Type']
        return response

    full_path = Path(settings.MEDIA_ROOT) / path
    if not full_path.is_file():
        raise Http404
    content_type, _ = mimetypes.guess_type(full_path.name)
    return FileResponse(full_path.open('rb'), content_type=content_type)


def healthz(request):
    """Для healthcheck контейнера и мониторинга: процесс жив и отвечает."""
    return HttpResponse('ok', content_type='text/plain')


def readyz(request):
    """Готовность (этап 11A): процесс отвечает И доступны PostgreSQL и Redis.
    Без подробностей ошибок наружу — только ok / fail по каждой зависимости."""
    import json

    from django.core.cache import cache
    from django.db import connection

    state = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        state['db'] = 'ok'
    except Exception:
        state['db'] = 'fail'
    try:
        cache.set('gp2:readyz', '1', 5)
        state['cache'] = 'ok' if cache.get('gp2:readyz') == '1' else 'fail'
    except Exception:
        state['cache'] = 'fail'
    ok = all(v == 'ok' for v in state.values())
    return HttpResponse(json.dumps(state), status=200 if ok else 503, content_type='application/json')

"""Данные модуля «Проекты» для интерфейса 2.0 (UI-03A).

Список с фильтрами и сортировкой, обзор карточки, этапы и команда.
Шаблоны только выводят готовые значения; доступ к проекту проверяют views
(руководитель — middleware, ПМ — pm_project_or_404, тимлид —
lead_project_or_404).
"""
from django.db.models import Case, Count, IntegerField, Prefetch, Q, Value, When
from django.db.models.functions import Coalesce

from apps.interns.services import branch_of
from apps.projects.models import Project, ProjectStage, ProjectStageKey, ProjectStatus, ProjectType
from apps.teams.models import TeamMember, TeamRole
from apps.teams.rules import DIRECTION_ROLES, NEW_TARGET_PER_DIRECTION, OLD_MAX_PER_DIRECTION

SORTS = {
    'name': 'name', 'start': 'start_date', 'progress': 'progress',
    'members': 'members_count', 'status': 'status', 'created': 'created_at', 'stage': 'stage_order',
}
# По умолчанию — по этапу жизненного цикла (основной, иначе подсказка), свежие выше
DEFAULT_SORT = 'stage'
ROLE_LABELS = dict(TeamRole.choices)
ROLE_LABELS.update({TeamRole.PROJECT_MANAGER: 'ПМ', TeamRole.TEAM_LEAD: 'Тимлид', TeamRole.OTHER: 'Другое'})
STATUS_TONES = {
    ProjectStatus.ACTIVE: 'primary', ProjectStatus.PAUSED: 'warning', ProjectStatus.COMPLETED: 'success',
    ProjectStatus.CANCELLED: 'danger', ProjectStatus.REFUSED: 'danger',
}
STAGE_TONES = {ProjectStage.Status.DONE: 'success', ProjectStage.Status.IN_PROGRESS: 'primary',
               ProjectStage.Status.NOT_STARTED: 'neutral'}


# ================================================================ список

def list_queryset(base=None):
    active_members = Q(team_members__status=TeamMember.Status.ACTIVE, team_members__intern__isnull=False)
    qs = (base if base is not None else Project.objects.active())
    stage_order = Case(
        *[When(stage_key=key, then=Value(index)) for index, key in enumerate(ProjectStageKey.values)],
        default=Value(len(ProjectStageKey.values)), output_field=IntegerField(),
    )
    return qs.select_related('client', 'project_type', 'primary_stage', 'flow').prefetch_related(
        Prefetch('team_members', queryset=TeamMember.objects.select_related('intern')),
    ).annotate(
        members_count=Count('team_members__intern', filter=active_members, distinct=True),
        stage_key=Coalesce('primary_stage__key', 'current_stage'),
    ).annotate(stage_order=stage_order)


def apply_filters(qs, params):
    """Фильтры списка: поиск, статус, этап, тип, ПМ, тимлид."""
    search = (params.get('q') or '').strip()
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(code__icontains=search)
                       | Q(client__organization__icontains=search))
    if params.get('status') in ProjectStatus.values:
        qs = qs.filter(status=params['status'])
    stage = params.get('primary_stage')
    if stage == 'unset':
        qs = qs.filter(primary_stage__isnull=True)
    elif stage in ProjectStageKey.values:
        qs = qs.filter(primary_stage__key=stage)
    if (params.get('type') or '').isdigit():
        qs = qs.filter(project_type_id=params['type'])
    for param, role in (('pm', TeamRole.PROJECT_MANAGER), ('lead', TeamRole.TEAM_LEAD)):
        if (params.get(param) or '').isdigit():
            qs = qs.filter(pk__in=TeamMember.objects.filter(
                role=role, status=TeamMember.Status.ACTIVE, intern_id=params[param],
            ).values('project_id'))
    return qs


def apply_sort(qs, value):
    value = value or DEFAULT_SORT
    field = SORTS.get(value.lstrip('-'))
    if not field:
        value, field = DEFAULT_SORT, SORTS[DEFAULT_SORT.lstrip('-')]
    return qs.order_by(('-' if value.startswith('-') else '') + field, '-created_at', 'pk'), value


def filter_options(scope_qs):
    """Значения для фильтров — только из проектов, видимых пользователю."""
    ids = scope_qs.values('pk')
    people = TeamMember.objects.filter(project_id__in=ids, status=TeamMember.Status.ACTIVE, intern__isnull=False)
    return {
        'statuses': ProjectStatus.choices,
        'stages': ProjectStageKey.choices,
        'types': ProjectType.objects.filter(projects__in=ids).distinct(),
        'pms': people.filter(role=TeamRole.PROJECT_MANAGER).values_list('intern_id', 'intern__full_name').distinct().order_by('intern__full_name'),
        'leads': people.filter(role=TeamRole.TEAM_LEAD).values_list('intern_id', 'intern__full_name').distinct().order_by('intern__full_name'),
    }


def decorate(projects):
    """Подписи для строк: ПМ, тимлиды, этап (выбран или нет)."""
    for project in projects:
        project.ui_pm = project.pm
        project.ui_leads = project.leads
        project.ui_stage, project.ui_stage_confirmed = stage_label(project)
        project.ui_status_tone = STATUS_TONES.get(project.status, 'neutral')
    return projects


def stage_label(project) -> tuple[str, bool]:
    """Этап проекта — один, выбранный вручную; не выбран — «Не выбран»."""
    if project.primary_stage_id:
        return project.primary_stage.get_key_display(), True
    return 'Не выбран', False


# ================================================================ карточка

def stage_rows(project) -> dict:
    """Все этапы (параллельные тоже) и прогресс по равным весам завершённых."""
    stages = list(project.stages.order_by('order'))
    working = [s for s in stages if s.key != ProjectStageKey.COMPLETED]
    done = sum(1 for s in working if s.status == ProjectStage.Status.DONE)
    for stage in stages:
        stage.ui_tone = STAGE_TONES.get(stage.status, 'neutral')
        stage.ui_is_primary = stage.pk == project.primary_stage_id
    return {
        'stages': stages,
        'done': done,
        'in_progress': sum(1 for s in working if s.status == ProjectStage.Status.IN_PROGRESS),
        'total': len(working),
        'progress': round(done / len(working) * 100) if working else 0,
    }


ALWAYS_SHOWN = (TeamRole.PROJECT_MANAGER, TeamRole.TEAM_LEAD, TeamRole.UXUI, TeamRole.BACKEND,
                TeamRole.FRONTEND, TeamRole.QA)


def _bot_started_chats(chats) -> set:
    """Кто из этих Telegram реально запускал бота 2.0 (ID из 1.0 сам по себе — нет)."""
    from apps.tgbot.models import OutboundMessage, TelegramUpdate

    if not chats:
        return set()
    wrote = {u for u, c in TelegramUpdate.objects.filter(user_id__in=chats, chat_id__in=chats).values_list('user_id', 'chat_id')
             if u == c}
    got = set(OutboundMessage.objects.filter(chat_id__in=chats, status=OutboundMessage.Status.SENT)
              .values_list('chat_id', flat=True))
    return wrote | got


def team(project, params=None, include_left=False) -> dict:
    """Команда по направлениям: новые / старые стажёры, лимиты состава."""
    from apps.dashboard.graduation import compute

    params = params or {}
    members = list(project.team_members.select_related('intern__specialization', 'user').order_by('role', 'intern__full_name'))
    active = [m for m in members if m.status == TeamMember.Status.ACTIVE]
    shown = members if include_left else active
    graduates = compute([m.intern_id for m in members if m.intern_id]).ids
    for member in members:
        member.ui_role = ROLE_LABELS.get(member.role, member.role)
        member.ui_old = bool(member.intern_id and member.intern_id in graduates)
        member.ui_city = branch_of(member.intern) if member.intern_id else ''
        member.ui_is_staff = member.role in (TeamRole.PROJECT_MANAGER, TeamRole.TEAM_LEAD)
    started = _bot_started_chats({m.intern.telegram_chat_id for m in members if m.intern_id and m.intern.telegram_chat_id})
    for member in members:
        chat = member.intern.telegram_chat_id if member.intern_id else None
        member.ui_bot = 'yes' if chat in started else ('legacy' if chat else 'no')

    direction = params.get('direction', '')
    kind = params.get('kind', '')
    bot = params.get('bot', '')
    filtered = [m for m in shown
                if (not direction or m.role == direction)
                and (not kind or (kind == 'old' and m.ui_old and not m.ui_is_staff)
                     or (kind == 'new' and not m.ui_old and not m.ui_is_staff))
                and (not bot or (bot == 'yes') == (m.ui_bot == 'yes'))]

    is_mobile = bool(project.project_type and project.project_type.is_mobile)
    always = [TeamRole.MOBILE if (is_mobile and r == TeamRole.FRONTEND) else r for r in ALWAYS_SHOWN]
    sections, order = [], [TeamRole.PROJECT_MANAGER, TeamRole.TEAM_LEAD, *DIRECTION_ROLES]
    for role in order:
        people = [m for m in filtered if m.role == role]
        if not people and (role not in always or direction or kind):
            continue
        in_direction = [m for m in active if m.role == role]
        old = sum(1 for m in in_direction if m.ui_old)
        new = len(in_direction) - old
        section = {'role': role, 'label': ROLE_LABELS.get(role, role), 'members': people,
                   'new': new, 'old': old, 'is_direction': role in DIRECTION_ROLES,
                   'new_label': f'новых {new}', 'old_label': f'старых {old}'}
        if role in DIRECTION_ROLES:
            section['old_over'] = old > OLD_MAX_PER_DIRECTION
            section['new_over_target'] = new > NEW_TARGET_PER_DIRECTION
        sections.append(section)

    interns_active = {m.intern_id for m in active if m.intern_id}
    direction_people = {m.intern_id for m in active if m.intern_id and m.role in DIRECTION_ROLES}
    bot_people = {m.intern_id: m.ui_bot for m in active if m.intern_id}
    return {
        'bot_connected': sum(1 for v in bot_people.values() if v == 'yes'), 'bot_total': len(bot_people),
        'sections': sections,
        'unique': len(interns_active),
        'new': len(direction_people - graduates),
        'old': len(direction_people & graduates),
        'left_count': sum(1 for m in members if m.status != TeamMember.Status.ACTIVE),
        'directions': [(r, ROLE_LABELS[r]) for r in order if any(m.role == r for m in members)],
        'filters': {'direction': direction, 'kind': kind, 'bot': bot},
        'include_left': include_left,
        'pm_members': [m for m in active if m.role == TeamRole.PROJECT_MANAGER],
        'lead_members': [m for m in active if m.role == TeamRole.TEAM_LEAD],
    }


def overview(project) -> dict:
    composition = team(project)
    return {
        'pm': composition['pm_members'],
        'leads': composition['lead_members'],
        'unique': composition['unique'],
        'new': composition['new'],
        'old': composition['old'],
        'stages': stage_rows(project),
        'last_report': project.reports.select_related('author').order_by('-date', '-created_at').first(),
        'links': [
            (label, url) for label, url in (
                ('Staging', project.staging_url), ('Production', project.production_url),
                ('Репозиторий бэкенда', project.github_url), ('Репозиторий фронтенда', project.frontend_repo_url),
                ('Figma', project.figma_url),
            ) if url
        ],
    }

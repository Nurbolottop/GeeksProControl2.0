"""Control: привязка Telegram-групп (руководитель) и подключение участников
(тимлид — в своём портале). Права проверяет apps.tgbot.groups."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.projects.models import Project, ProjectStatus
from apps.tgbot import groups
from apps.tgbot.models import BindCode, GroupMembership, TelegramGroup


def _flash(request, error):
    messages.error(request, ' '.join(getattr(error, 'messages', None) or [str(error)]))


def context(request) -> dict:
    from django.conf import settings
    from django.utils import timezone

    items = list(TelegramGroup.objects.exclude(status=TelegramGroup.Status.UNBOUND)
                 .select_related('project').prefetch_related('topics'))
    bound = {g.project_id for g in items if g.project_id}
    return {
        'groups_block': True, 'tg_groups': items,
        'tg_codes': BindCode.objects.filter(used_at__isnull=True, expires_at__gt=timezone.now()).select_related('project'),
        'tg_projects': Project.objects.active().filter(status__in=[ProjectStatus.ACTIVE, ProjectStatus.PAUSED])
        .exclude(pk__in=bound).order_by('name'),
        'tg_service_kinds': [(k, label) for k, label in TelegramGroup.Kind.choices if k != TelegramGroup.Kind.PROJECT],
        'tg_bot_username': settings.TELEGRAM_BOT_USERNAME,
        'tg_sales_chat': bool(settings.SALES_TELEGRAM_CHAT_ID),
        'tg_access': GroupMembership.objects.exclude(status=GroupMembership.Status.REMOVED)
        .select_related('member__intern', 'member__project')[:100],
    }


def _head(request):
    from apps.accounts.access import has_full_access

    if not has_full_access(request.user):
        raise PermissionDenied


@login_required
@require_POST
def issue_code(request):
    _head(request)
    kind = request.POST.get('kind', TelegramGroup.Kind.PROJECT)
    project = Project.objects.filter(pk=request.POST.get('project') or 0).first()
    try:
        code = groups.issue_code(request.user, project=project, kind=kind)
    except (groups.GroupError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, f'Код привязки: {code.code} (30 минут). Отправьте в группе: /bind {code.code}')
    return redirect('tgbot:reconcile')


@login_required
@require_POST
def group_action(request, pk, action):
    _head(request)
    group = get_object_or_404(TelegramGroup, pk=pk)
    try:
        if action == 'confirm':
            groups.confirm(group, request.user)
            messages.success(request, f'Группа «{group}» привязана. Темы — по направлениям проекта.')
        elif action == 'topics':
            made = groups.sync_topics(group)
            messages.success(request, f'Темы проверены, создано: {len(made)}.')
        elif action == 'unbind':
            groups.unbind(group, request.user)
            messages.success(request, f'Группа «{group}» отвязана. История сохранена.')
        else:
            raise PermissionDenied
    except groups.GroupError as error:
        _flash(request, error)
    return redirect('tgbot:reconcile')


def _mark(request, pk, status, back):
    access = get_object_or_404(GroupMembership.objects.select_related('member'), pk=pk)
    if status not in (GroupMembership.Status.INVITED, GroupMembership.Status.JOINED, GroupMembership.Status.REMOVED):
        raise PermissionDenied
    try:
        groups.mark(access, request.user, status)
    except (groups.GroupError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, f'{access.member.intern.full_name}: {GroupMembership.Status(status).label.lower()}.')
    return redirect(back)


@login_required
@require_POST
def head_mark(request, pk, status):
    _head(request)
    return _mark(request, pk, status, 'tgbot:reconcile')


@login_required
def lead_groups(request):
    """Портал тимлида: подключение участников его проектов к группам."""
    from apps.teams.models import TeamMember, TeamRole

    projects = TeamMember.objects.filter(intern__user=request.user, role=TeamRole.TEAM_LEAD,
                                         status=TeamMember.Status.ACTIVE).values_list('project_id', flat=True)
    groups.reconcile(project_ids=list(projects))
    items = list(GroupMembership.objects.filter(member__project_id__in=projects)
                 .exclude(status=GroupMembership.Status.REMOVED)
                 .select_related('member__intern', 'member__project', 'group').order_by('status', 'member__project__name'))
    for item in items:
        item.ui_actions = {
            GroupMembership.Status.TO_INVITE: [('invited', 'Приглашение отправлено'), ('joined', 'Вступил(а)')],
            GroupMembership.Status.INVITED: [('joined', 'Вступил(а)')],
            GroupMembership.Status.ACCESS_ENDED: [('removed', 'Удалён(а) из группы')],
        }.get(item.status, [])
    return render(request, 'tgbot/lead_groups.html', {
        'items': items, 'crumbs': [{'label': 'Мои проекты', 'href': reverse('lead_portal:dashboard')},
                                   {'label': 'Telegram-группы', 'href': ''}],
    })


@login_required
@require_POST
def lead_mark(request, pk, status):
    return _mark(request, pk, status, 'lead_portal:telegram_groups')

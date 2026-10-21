"""Видеособрания в Control (этап 9): тимлид (/lead/meetings/), ПМ
(/pm/meetings/), руководитель (/video/). Права — apps.video.services."""
from __future__ import annotations

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.projects.models import Project
from apps.teams.models import TeamMember, TeamRole
from apps.video import services
from apps.video.models import VideoMeeting

PREFIX = {'head': 'video', 'lead': 'lead_portal', 'pm': 'pm_portal'}
DIRECTIONS = dict(TeamRole.choices)
STATUS_TONES = {'planned': 'warning', 'held': 'success', 'cancelled': 'neutral'}


def _flash(request, error):
    messages.error(request, ' '.join(getattr(error, 'messages', None) or [str(error)]))


def _url(scope, name, *args):
    return reverse(f'{PREFIX[scope]}:{"meeting_" + name if scope != "head" else name}', args=args)


def _scope_qs(request, scope):
    user = request.user
    qs = VideoMeeting.objects.select_related('project', 'host')
    if scope == 'head':
        from apps.accounts.access import has_full_access

        if not has_full_access(user):
            raise PermissionDenied
        return qs
    intern = getattr(user, 'intern_profile', None)
    if intern is None:
        raise PermissionDenied
    if scope == 'pm':
        projects = TeamMember.objects.filter(intern=intern, role=TeamRole.PROJECT_MANAGER,
                                             status=TeamMember.Status.ACTIVE).values_list('project_id', flat=True)
        return qs.filter(project_id__in=projects)
    from apps.teams.staff import lead_member_direction

    pairs = Q(pk__in=[])
    for member in TeamMember.objects.filter(intern=intern, role=TeamRole.TEAM_LEAD,
                                            status=TeamMember.Status.ACTIVE).select_related('intern__specialization'):
        pairs |= Q(project_id=member.project_id, direction=lead_member_direction(member))
    return qs.filter(pairs | Q(host=intern))


def _crumbs(scope, *extra):
    home = {'head': ('Главная', reverse('dashboard')), 'lead': ('Мои проекты', reverse('lead_portal:dashboard')),
            'pm': ('Мои проекты', reverse('pm_portal:dashboard'))}[scope]
    items = [{'label': home[0], 'href': home[1]}, {'label': 'Собрания', 'href': _url(scope, 'list')}]
    return items + [{'label': label, 'href': href} for label, href in extra]


@login_required
def meeting_list(request, scope='head'):
    from apps.dashboard import metrics

    qs = _scope_qs(request, scope).annotate(ui_people=Count('participants', distinct=True))
    params = request.GET
    if (params.get('project') or '').isdigit():
        qs = qs.filter(project_id=params['project'])
    if params.get('direction') in DIRECTIONS:
        qs = qs.filter(direction=params['direction'])
    if (params.get('host') or '').isdigit():
        qs = qs.filter(host_id=params['host'])
    if params.get('kind') in VideoMeeting.Kind.values:
        qs = qs.filter(kind=params['kind'])
    week = None
    if params.get('week'):
        week = metrics.parse_week(params.get('week'))
        qs = qs.filter(week_start=week.start)
    meetings = list(qs.order_by('-starts_at')[:200])
    for m in meetings:
        m.ui_url = _url(scope, 'detail', m.pk)
        m.ui_tone = STATUS_TONES.get(m.status, 'neutral')
        m.ui_direction = DIRECTIONS.get(m.direction, 'тимлиды проекта') if m.direction else 'тимлиды проекта'
    context = {
        'scope': scope, 'meetings': meetings, 'params': params, 'week': week,
        'weeks': metrics.recent_weeks(8), 'kinds': VideoMeeting.Kind.choices,
        'directions': [(k, v) for k, v in TeamRole.choices if k not in ('pm', 'team_lead')],
        'create_url': _url(scope, 'new') if scope != 'head' else '',
        'crumbs': _crumbs(scope)[:-1] + [{'label': 'Собрания', 'href': ''}],
    }
    if scope == 'head':
        from apps.interns.models import Intern

        context['projects'] = Project.objects.active().order_by('name')
        context['hosts'] = Intern.objects.filter(video_meetings_hosted__isnull=False).distinct().order_by('full_name')
    try:
        from apps.video import obligations

        context.update(obligations.list_context(request, scope, week))
    except ImportError:
        pass
    return render(request, 'video/list.html', context)


def _pairs(intern):
    """(проект, направление) — где пользователь активный тимлид в идущем проекте."""
    from apps.teams.staff import lead_member_direction

    seen, result = set(), []
    for member in (TeamMember.objects.filter(intern=intern, role=TeamRole.TEAM_LEAD, status=TeamMember.Status.ACTIVE,
                                             project__status__in=services.WRITABLE)
                   .select_related('project', 'intern__specialization').order_by('project__name')):
        key = (member.project_id, lead_member_direction(member))
        if key[1] and key not in seen:
            seen.add(key)
            result.append({'key': f'{key[0]}:{key[1]}', 'project': member.project, 'direction': key[1],
                           'label': f'{member.project.name} · {DIRECTIONS.get(key[1], key[1])}'})
    return result


def _parse_start(raw_date, raw_time):
    try:
        day = datetime.date.fromisoformat(raw_date or '')
        moment = datetime.time.fromisoformat(raw_time or '')
    except ValueError:
        return None
    return timezone.make_aware(datetime.datetime.combine(day, moment))


@login_required
def meeting_new(request, scope='lead'):
    intern = getattr(request.user, 'intern_profile', None)
    if scope not in ('lead', 'pm') or intern is None:
        raise PermissionDenied
    if scope == 'lead':
        pairs = _pairs(intern)
        chosen = next((p for p in pairs if p['key'] == (request.POST.get('pair') or request.GET.get('pair'))),
                      pairs[0] if pairs else None)
        people = list(services.direction_members(chosen['project'], chosen['direction'])) if chosen else []
        kinds = [(k, v) for k, v in VideoMeeting.Kind.choices if k != VideoMeeting.Kind.PM_LEADS]
    else:
        projects = list(Project.objects.filter(team_members__intern=intern, team_members__role=TeamRole.PROJECT_MANAGER,
                                               team_members__status=TeamMember.Status.ACTIVE,
                                               status__in=services.WRITABLE).distinct().order_by('name'))
        pairs = [{'key': str(p.pk), 'project': p, 'direction': '', 'label': p.name} for p in projects]
        chosen = next((p for p in pairs if p['key'] == (request.POST.get('pair') or request.GET.get('pair'))),
                      pairs[0] if pairs else None)
        people = list(services.project_leads(chosen['project'])) if chosen else []
        kinds = [(VideoMeeting.Kind.PM_LEADS, VideoMeeting.Kind.PM_LEADS.label)]
    if request.method == 'POST' and chosen:
        try:
            meeting = services.create_meeting(
                request.user, chosen['project'], kind=request.POST.get('kind', kinds[0][0]),
                topic=request.POST.get('topic'), direction=chosen['direction'],
                starts_at=_parse_start(request.POST.get('date'), request.POST.get('time')),
                duration_minutes=request.POST.get('duration') or 60, intern_ids=request.POST.getlist('people'))
        except (services.MeetingError, PermissionDenied) as error:
            _flash(request, error)
        else:
            messages.success(request, f'Собрание «{meeting.topic}» назначено, приглашения поставлены в очередь.')
            return redirect(_url(scope, 'detail', meeting.pk))
    return render(request, 'video/new.html', {
        'scope': scope, 'pairs': pairs, 'chosen': chosen, 'people': people, 'kinds': kinds,
        'today': timezone.localdate(), 'post': request.POST,
        'crumbs': _crumbs(scope, ('Новое собрание', '')),
    })


def _meeting(request, scope, pk):
    meeting = get_object_or_404(_scope_qs(request, scope), pk=pk)
    if scope != 'head' and not services.can_view(request.user, meeting):
        raise Http404
    return meeting


@login_required
def meeting_detail(request, pk, scope='head'):
    from apps.audit.models import AuditLog

    meeting = _meeting(request, scope, pk)
    participants = list(meeting.participants.select_related('intern'))
    context = {
        'scope': scope, 'm': meeting, 'participants': participants,
        'tone': STATUS_TONES.get(meeting.status, 'neutral'),
        'direction': DIRECTIONS.get(meeting.direction, 'тимлиды проекта') if meeting.direction else 'тимлиды проекта',
        'can_manage': services.can_manage(request.user, meeting),
        'can_mark': meeting.status == 'planned' and meeting.starts_at <= timezone.now() and (
            services.can_manage(request.user, meeting) or scope == 'head'),
        'is_host': getattr(getattr(request.user, 'intern_profile', None), 'pk', None) == meeting.host_id,
        'urls': {a: _url(scope, 'action', meeting.pk, a) for a in ('cancel', 'held', 'reschedule')},
        'join_url': _url(scope, 'join', meeting.pk) if scope != 'head' else '',
        'history': AuditLog.objects.filter(object_type='VideoMeeting', object_id=str(meeting.pk))
        .select_related('user').order_by('-created_at')[:50],
        'crumbs': _crumbs(scope, (meeting.topic, '')),
    }
    try:
        from apps.video import attendance

        context.update(attendance.detail_context(request, scope, meeting, participants))
    except ImportError:
        pass
    return render(request, 'video/detail.html', context)


@login_required
@require_POST
def meeting_action(request, pk, action, scope='head'):
    meeting = _meeting(request, scope, pk)
    try:
        if action == 'cancel':
            services.cancel(meeting, request.user, request.POST.get('reason'))
            messages.success(request, 'Собрание отменено.')
        elif action == 'held':
            services.mark_held(meeting, request.user, request.POST.get('note'))
            messages.success(request, 'Проведение отмечено вручную.')
        elif action == 'reschedule':
            start = _parse_start(request.POST.get('date'), request.POST.get('time'))
            if start is None:
                raise services.MeetingError('Укажите дату и время.')
            services.reschedule(meeting, request.user, start)
            messages.success(request, 'Собрание перенесено.')
        else:
            raise Http404
    except (services.MeetingError, PermissionDenied) as error:
        _flash(request, error)
    return redirect(_url(scope, 'detail', meeting.pk))


@login_required
def meeting_join(request, pk, scope='lead'):
    meeting = _meeting(request, scope, pk)
    try:
        return redirect(services.host_join_url(meeting, request.user))
    except Exception as error:  # ProviderError, PermissionDenied
        _flash(request, error)
        return redirect(_url(scope, 'detail', meeting.pk))


@login_required
@require_POST
def meeting_correct(request, pk, participant_pk, scope='head'):
    from apps.video import attendance

    meeting = _meeting(request, scope, pk)
    participant = get_object_or_404(meeting.participants, pk=participant_pk)
    try:
        attendance.correct(participant, request.user, request.POST.get('value'), request.POST.get('reason'))
    except (attendance.AttendanceError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, f'{participant.person_name}: посещаемость исправлена.')
    return redirect(_url(scope, 'detail', meeting.pk))


def _plain(text, status):
    response = HttpResponse(text, status=status, content_type='text/plain; charset=utf-8')
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def public_join(request, token):
    """Ссылка из приглашения: JWT не выдаёт (этап 10A) — вход только кнопкой в боте."""
    try:
        participant = services.participant_for_token(token)
    except PermissionDenied as error:
        return _plain(str(error), 403)
    except Exception as error:  # MeetingError
        return _plain(' '.join(getattr(error, 'messages', None) or [str(error)]), 409)
    return _plain(f'Собрание «{participant.meeting.topic}». Вход подтверждается в Telegram-боте GeeksPro: '
                  'нажмите «Войти на собрание» в сообщении-приглашении — бот выдаст одноразовую ссылку.', 200)


def public_enter(request, code):
    """Одноразовый код из бота → видеосервис. JWT уходит только в Location этого
    ответа: без кеша и без Referer, в журналы Control не пишется."""
    try:
        target = services.redeem_entry(code)
    except PermissionDenied as error:
        return _plain(str(error), 403)
    except Exception as error:  # MeetingError, ProviderError (release-gate)
        return _plain(' '.join(getattr(error, 'messages', None) or [str(error)]), 409)
    response = redirect(target)
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def portal_urlpatterns(scope):
    from django.urls import path

    kw = {'scope': scope}
    return [
        path('meetings/', meeting_list, kw, name='meeting_list'),
        path('meetings/new/', meeting_new, kw, name='meeting_new'),
        path('meetings/<int:pk>/', meeting_detail, kw, name='meeting_detail'),
        path('meetings/<int:pk>/join/', meeting_join, kw, name='meeting_join'),
        path('meetings/<int:pk>/do/<str:action>/', meeting_action, kw, name='meeting_action'),
        path('meetings/<int:pk>/participant/<int:participant_pk>/correct/', meeting_correct, kw,
             name='meeting_correct'),
    ]

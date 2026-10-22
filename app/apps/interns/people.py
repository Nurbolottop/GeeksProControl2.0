"""Модуль «Стажёры и выпускники» (UI-04A): данные для списка, карточки и
выпускников. Один профиль — Intern; периоды — Internship; участия —
TeamMember; выпуск — только apps.dashboard.graduation (тот же источник,
что у Dashboard). Здесь ничего не меняется в БД.

Стажёр в списке — карточка не в архиве и не тимлид (тимлиды — сотрудники),
как и в показателях Dashboard (apps.dashboard.metrics).
"""
from __future__ import annotations

from collections import defaultdict

from django.db.models import Count, Exists, OuterRef, Q

from apps.interns.models import (
    AVAILABLE_STATUSES,
    GraduateStatus,
    Intern,
    InternshipRequest,
    InternStatus,
    InternWarning,
    ResumeBankStatus,
)
from apps.interns.services import BRANCH_BISHKEK, BRANCH_OSH, BRANCHES, branch_filter, branch_of
from apps.teams.models import TeamMember, TeamRole

SORTS = {'name': 'full_name', 'start': 'internship_start_date', 'status': 'status',
         'created': 'created_at', 'warnings': 'warnings_count'}
DEFAULT_SORT = 'name'
STATUS_TONES = {
    InternStatus.ACTIVE: 'success', InternStatus.PAUSED: 'warning', InternStatus.WAITING: 'neutral',
    InternStatus.READY: 'info', InternStatus.EMPLOYABLE: 'primary', InternStatus.EMPLOYED: 'primary',
    InternStatus.DROPPED: 'danger', InternStatus.EXPELLED: 'danger', InternStatus.CANCELLED: 'neutral',
}
LEFT_REASON_LABELS = dict(TeamMember.LeftReason.choices)


def staff_ids() -> set:
    from apps.teams.selectors import staff_intern_ids

    return staff_intern_ids()


def graduate_ids() -> set:
    from apps.dashboard.graduation import compute

    return compute().ids


# ------------------------------------------------------------------- список

def bot_started_exists():
    """Человек действительно запускал бота 2.0: писал ему в личку или получил от него сообщение.
    Telegram ID из 1.0 (старый бот) сам по себе этого не значит — новый бот не может написать первым."""
    from apps.tgbot.models import OutboundMessage, TelegramUpdate

    chat = OuterRef('telegram_chat_id')
    return (Exists(TelegramUpdate.objects.filter(user_id=chat, chat_id=chat))
            | Exists(OutboundMessage.objects.filter(chat_id=chat, status=OutboundMessage.Status.SENT)))


def list_queryset(staff=None):
    active_member = TeamMember.objects.filter(intern=OuterRef('pk'), status=TeamMember.Status.ACTIVE,
                                              project__isnull=False)
    return (
        Intern.objects.active().exclude(pk__in=staff_ids() if staff is None else staff)
        .select_related('specialization')
        .annotate(
            warnings_count=Count('warnings', filter=Q(warnings__status=InternWarning.Status.ACTIVE), distinct=True),
            on_project=Exists(active_member),
            bot_started=bot_started_exists(),
        )
    )


def _phone_digits():
    """Телефон без пробелов, скобок и дефисов — чтобы «0555123456» находил «+996 555 12-34-56»."""
    from django.db.models import F, Func, Value

    return Func(F('phone'), Value('[^0-9]'), Value(''), Value('g'), function='regexp_replace')


def search_filter(qs, search):
    search = (search or '').strip()
    if not search:
        return qs
    digits = ''.join(ch for ch in search if ch.isdigit())
    query = Q(full_name__icontains=search) | Q(telegram__icontains=search.lstrip('@'))
    if len(digits) >= 3:
        qs = qs.annotate(phone_digits=_phone_digits())
        query |= Q(phone_digits__contains=digits.lstrip('0') or digits)
    return qs.filter(query)


def apply_filters(qs, params, graduates: set):
    qs = search_filter(qs, params.get('q'))
    if params.get('branch') in BRANCHES:
        qs = qs.filter(branch_filter(params['branch']))
    if (params.get('specialization') or '').isdigit():
        qs = qs.filter(specialization_id=params['specialization'])
    status = params.get('status')
    if status == InternStatus.WAITING:
        # «Ожидает стажировки» — кто ещё не попал ни в один проект
        qs = qs.filter(status=status, on_project=False)
    elif status in InternStatus.values:
        qs = qs.filter(status=status)
    availability = params.get('availability')  # прежний фильтр «Занятость»
    if availability == 'busy':
        qs = qs.filter(on_project=True)
    elif availability == 'free':
        qs = qs.filter(on_project=False, status__in=AVAILABLE_STATUSES).exclude(pk__in=graduates)
    elif availability == 'paused':
        qs = qs.filter(status=InternStatus.PAUSED)
    elif availability == 'graduates':
        qs = qs.filter(pk__in=graduates)
    elif availability == 'waiting':
        qs = qs.filter(status=InternStatus.WAITING)
    kind = params.get('kind')
    if kind == 'old':
        qs = qs.filter(pk__in=graduates)
    elif kind == 'new':
        qs = qs.exclude(pk__in=graduates)
    if (params.get('project') or '').isdigit():
        qs = qs.filter(pk__in=TeamMember.objects.filter(
            project_id=params['project'], status=TeamMember.Status.ACTIVE).values('intern_id'))
    telegram = params.get('telegram')
    if telegram == 'yes':
        qs = qs.filter(telegram_chat_id__isnull=False, bot_started=True)
    elif telegram == 'legacy':
        qs = qs.filter(telegram_chat_id__isnull=False, bot_started=False)
    elif telegram == 'no':
        qs = qs.filter(Q(telegram_chat_id__isnull=True) | Q(bot_started=False))
    return qs


def apply_sort(qs, value):
    value = value or DEFAULT_SORT
    field = SORTS.get(value.lstrip('-'))
    if not field:
        value, field = DEFAULT_SORT, SORTS[DEFAULT_SORT]
    return qs.order_by(('-' if value.startswith('-') else '') + field, 'full_name', 'pk'), value


def filter_options(staff=None):
    from apps.projects.models import Project, ProjectStatus
    from apps.training.models import Specialization

    return {
        'branches': [BRANCH_BISHKEK, BRANCH_OSH],
        'specializations': Specialization.objects.order_by('name'),
        'statuses': InternStatus.choices,
        # только проекты, где сейчас кто-то есть
        'projects': Project.objects.active().filter(
            status__in=[ProjectStatus.ACTIVE, ProjectStatus.PAUSED],
            team_members__status=TeamMember.Status.ACTIVE, team_members__intern__isnull=False,
        ).distinct().order_by('name').only('pk', 'name'),
    }


def decorate(people, graduates: set):
    """Текущие проекты, ответственные тимлиды, город — без N+1 (3 запроса)."""
    people = list(people)
    ids = [p.pk for p in people]
    memberships = defaultdict(list)
    for member in (TeamMember.objects.filter(intern_id__in=ids, status=TeamMember.Status.ACTIVE,
                                             project__isnull=False)
                   .select_related('project').order_by('joined_at', 'pk')):
        memberships[member.intern_id].append(member)
    project_ids = {m.project_id for items in memberships.values() for m in items}
    leads = defaultdict(list)
    for lead in (TeamMember.objects.filter(project_id__in=project_ids, role=TeamRole.TEAM_LEAD,
                                           status=TeamMember.Status.ACTIVE, intern__isnull=False)
                 .select_related('intern__specialization')):
        leads[lead.project_id].append(lead.intern)
    for person in people:
        person.ui_memberships = memberships.get(person.pk, [])
        person.ui_is_old = person.pk in graduates
        person.ui_branch = branch_of(person)
        person.ui_status_tone = STATUS_TONES.get(person.status, 'neutral')
        person.ui_leads = responsible_leads(person, person.ui_memberships, leads)
    return people


def responsible_leads(person, memberships, leads_by_project) -> list:
    """Тимлид направления человека в его текущих проектах (если направление
    тимлида не совпало — все тимлиды проекта)."""
    result = []
    for member in memberships:
        candidates = leads_by_project.get(member.project_id, [])
        same = [lead for lead in candidates if lead.specialization_id and lead.specialization_id == person.specialization_id]
        for lead in same or candidates:
            if lead not in result:
                result.append(lead)
    return result


# ------------------------------------------------------------------ карточка

def card(person: Intern) -> dict:
    from apps.dashboard.graduation import compute
    from apps.interns import internships

    grad = compute([person.pk])
    members = list(person.team_memberships.select_related('project', 'project__client').order_by('-joined_at', '-pk'))
    active = [m for m in members if m.status == TeamMember.Status.ACTIVE and m.project_id]
    leads = defaultdict(list)
    for lead in (TeamMember.objects.filter(project_id__in=[m.project_id for m in active], role=TeamRole.TEAM_LEAD,
                                           status=TeamMember.Status.ACTIVE, intern__isnull=False)
                 .select_related('intern__specialization')):
        leads[lead.project_id].append(lead.intern)
    from apps.projects.models import ProjectStatus

    for member in members:
        member.ui_leads = leads.get(member.project_id, [])
        member.ui_reason = LEFT_REASON_LABELS.get(member.left_reason, '') if member.status != 'active' else ''
        member.ui_delivered = (member.status != 'active' and member.project_id is not None
                               and member.project.status == ProjectStatus.COMPLETED
                               and member.left_reason in (TeamMember.LeftReason.PROJECT_ENDED, ''))
        member.ui_remove_label = f'Снять с «{member.project.name}»' if member.project_id else 'Снять'
    periods = list(person.internships.select_related('certificate').order_by('-number'))
    for period in periods:
        period.ui_certificate = getattr(period, 'certificate', None)
    requests = list(person.internship_requests.select_related('decided_by', 'chosen_project').order_by('-created_at'))
    return {
        'bot_started': bool(person.telegram_chat_id) and Intern.objects.filter(pk=person.pk).annotate(
            started=bot_started_exists()).values_list('started', flat=True).first(),
        'person': person,
        'is_graduate': person.pk in grad.ids,
        'first_graduation': grad.first_dates.get(person.pk),
        'graduated_projects': grad.projects.get(person.pk, []),
        'graduation_review': [item for item in grad.review if item['intern_id'] == person.pk],
        'graduation_evidence': [_evidence_label(kind, day) for kind, day in grad.evidence.get(person.pk, [])],
        'branch': branch_of(person),
        'status_tone': STATUS_TONES.get(person.status, 'neutral'),
        'active_memberships': active,
        'past_memberships': [m for m in members if m not in active],
        'current_period': internships.current(person),
        'periods': periods,
        'requests': requests,
        'pending_requests': [r for r in requests if r.status == InternshipRequest.Status.PENDING],
        'warnings': list(person.warnings.select_related('project', 'issued_by').order_by('-created_at')),
        'active_warnings': person.warnings.filter(status=InternWarning.Status.ACTIVE).count(),
        'pending_case': person.discipline_cases.filter(status='pending').first(),
        'leave_allowed': _leave_allowed(person, active, grad),
        'is_stopped': person.status in (InternStatus.EXPELLED, InternStatus.CANCELLED, InternStatus.DROPPED),
        'evaluations': list(person.evaluations.select_related('project', 'evaluator')),
        'timeline': timeline(person, members, periods, requests, grad),
        'activity': activity_label(person, bool(active), person.pk in grad.ids),
    }


EVIDENCE = {'completed': 'был в команде при официальном закрытии проекта',
            'confirmed': 'сохранённая отметка выпуска', 'promoted': 'повышен(а) до тимлида'}


def _evidence_label(kind, day):
    text = EVIDENCE.get(kind, kind)
    return f'{text} ({day:%d.%m.%Y})' if day else text


def activity_label(person, on_project: bool, is_graduate: bool) -> tuple[str, str]:
    """Дальнейшая деятельность: (подпись, тон) — по фактическим полям."""
    if person.status == InternStatus.EMPLOYED:
        return 'Трудоустроен(а)', 'primary'
    if person.status == InternStatus.PAUSED:
        return 'Стажировка заморожена', 'warning'
    if person.status == InternStatus.EXPELLED:
        return 'Отчислен(а)', 'danger'
    if person.status == InternStatus.CANCELLED:
        return 'Отмена — ушёл(ла) самостоятельно', 'neutral'
    if on_project:
        return 'Участвует в проекте', 'success'
    if person.resume_bank_status and person.resume_bank_status != ResumeBankStatus.APPROVED:
        return person.get_resume_bank_status_display(), 'info'  # заявка в банк резюме на проверке/доработке
    if person.in_resume_bank or person.resume_bank_status == ResumeBankStatus.APPROVED:
        return 'В банке резюме', 'info'
    if person.graduate_status == GraduateStatus.WAITING:
        return 'Ждёт следующий проект', 'neutral'
    if person.graduate_status == GraduateStatus.PENDING:
        return 'Решение не принято', 'warning'
    if person.graduate_status == GraduateStatus.DECLINED:
        return 'Не продолжает стажировку', 'neutral'
    if person.status == InternStatus.DROPPED:
        return 'Выбыл(а)', 'danger'
    return ('Без проекта', 'neutral') if is_graduate else (person.get_status_display(), 'neutral')


def timeline(person, members, periods, requests, grad) -> list[dict]:
    """История одним списком (новые сверху). Только записанные события —
    без вымышленных дат: у события без даты дата не показывается."""
    from apps.audit.models import AuditLog

    events = [{'at': person.created_at, 'title': 'Профиль создан', 'tone': 'neutral', 'icon': 'user'}]
    for period in periods:
        if period.started_on:
            events.append({'at': period.started_on, 'title': f'Начало стажировки №{period.number}',
                           'meta': period.get_source_display(), 'tone': 'primary', 'icon': 'calendar'})
        if period.end_reason:
            events.append({'at': period.ended_on, 'title': f'Стажировка №{period.number}: {period.get_end_reason_display().lower()}',
                           'meta': period.note, 'tone': 'neutral', 'icon': 'flag'})
    for member in members:
        name = member.project.name if member.project_id else 'без проекта'
        if member.joined_at:
            events.append({'at': member.joined_at, 'title': f'Вступил(а) в «{name}»',
                           'meta': member.get_role_display(), 'tone': 'success', 'icon': 'folder'})
        if member.status != TeamMember.Status.ACTIVE:
            reason = LEFT_REASON_LABELS.get(member.left_reason, 'причина не указана')
            events.append({'at': member.left_at, 'title': f'Вышел(а) из «{name}»', 'meta': reason,
                           'tone': 'warning' if member.left_reason == TeamMember.LeftReason.FROZEN else 'neutral',
                           'icon': 'log-out'})
    for project_id, name, day in grad.projects.get(person.pk, []):
        events.append({'at': day, 'title': f'Выпуск: проект «{name}» закрыт', 'tone': 'success', 'icon': 'graduation-cap'})
    for req in requests:
        events.append({'at': req.created_at, 'title': f'Заявка «{req.get_kind_display().lower()}»',
                       'meta': req.reason, 'tone': 'neutral', 'icon': 'inbox'})
        if req.decided_at:
            events.append({'at': req.decided_at, 'title': f'{req.get_kind_display()}: {req.get_status_display().lower()}',
                           'meta': req.decision_comment, 'tone': 'success' if req.status == 'approved' else 'neutral',
                           'icon': 'check-circle'})
    for log in (AuditLog.objects.filter(object_type='Intern', object_id=str(person.pk))
                .select_related('user').order_by('-created_at')[:100]):
        change = ' → '.join(v for v in (log.old_value, log.new_value) if v)
        events.append({'at': log.created_at, 'title': log.action,
                       'meta': ' · '.join(v for v in (change, log.reason, log.user.display_name if log.user else '') if v),
                       'tone': 'neutral', 'icon': 'clock'})
    for evaluation in person.evaluations.select_related('project', 'evaluator'):
        events.append({'at': evaluation.created_at, 'title': f'Оценка {evaluation.average}',
                       'meta': evaluation.project.name if evaluation.project_id else '', 'tone': 'info', 'icon': 'check'})
    for warning in person.warnings.all():
        events.append({'at': warning.created_at, 'title': 'Предупреждение', 'meta': warning.reason,
                       'tone': 'danger', 'icon': 'alert-triangle'})

    import datetime

    from django.utils import timezone

    def key(event):
        value = event['at']
        if value is None:
            return datetime.datetime.min
        if isinstance(value, datetime.datetime):
            return (timezone.localtime(value) if timezone.is_aware(value) else value).replace(tzinfo=None)
        return datetime.datetime.combine(value, datetime.time.max)
    return sorted(events, key=key, reverse=True)


# ---------------------------------------------------------------- выпускники

ACTIVITY_FILTERS = [
    ('project', 'Участвует в проекте'), ('waiting', 'Ждёт следующий проект'), ('pending', 'Решение не принято'),
    ('bank', 'В банке резюме'), ('declined', 'Не продолжает'), ('employed', 'Трудоустроен(а)'),
    ('paused', 'Заморожен(а)'),
]


def graduates(params) -> dict:
    """Выпускники — люди с подтверждённым выпуском (graduation.compute).

    Статус выпускника постоянный: человек остаётся в списке и тогда, когда
    снова участвует в проектах. Неоднозначные записи сюда не попадают.
    """
    from apps.dashboard.graduation import compute

    grad = compute()
    staff = staff_ids()
    qs = (Intern.objects.active().filter(pk__in=grad.ids).exclude(pk__in=staff)
          .select_related('specialization'))
    qs = search_filter(qs, params.get('q'))
    if params.get('branch') in BRANCHES:
        qs = qs.filter(branch_filter(params['branch']))
    if (params.get('specialization') or '').isdigit():
        qs = qs.filter(specialization_id=params['specialization'])
    people = decorate(qs.order_by('full_name', 'pk'), grad.ids)
    for person in people:
        person.ui_first_graduation = grad.first_dates.get(person.pk)
        person.ui_graduated_projects = grad.projects.get(person.pk, [])
        person.ui_activity = activity_label(person, bool(person.ui_memberships), True)
        person.ui_activity_key = _activity_key(person)
    activity = params.get('activity') or {'pending': 'pending', 'waiting': 'waiting', 'declined': 'declined'}.get(
        params.get('status', ''), '')  # совместимость со старыми ссылками ?status=
    if activity:
        people = [p for p in people if p.ui_activity_key == activity]
    return {'people': people, 'review_count': len([r for r in grad.review if r['intern_id'] not in staff]),
            'activity': activity, 'total': len(grad.ids - staff)}


def _activity_key(person) -> str:
    if person.status == InternStatus.EMPLOYED:
        return 'employed'
    if person.status == InternStatus.PAUSED:
        return 'paused'
    if person.ui_memberships:
        return 'project'
    if person.in_resume_bank or person.resume_bank_status:
        return 'bank'
    return {GraduateStatus.WAITING: 'waiting', GraduateStatus.PENDING: 'pending',
            GraduateStatus.DECLINED: 'declined'}.get(person.graduate_status, '')


def _leave_allowed(person, active, grad) -> bool:
    """Тот же критерий, что discipline.leave_allowed, без лишних запросов."""
    from apps.teams.rules import RULE_ROLES

    return person.pk in grad.ids and len({m.project_id for m in active if m.role in RULE_ROLES}) >= 2

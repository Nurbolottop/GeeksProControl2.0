"""Показатели главной страницы руководителя (UI-02).

Всё считается из фактических данных; там, где данных нет, возвращается
None — шаблон показывает «Нет данных», а не ноль.

Определения (они же — в docs/dashboard-metrics.md):

* Стажёр — карточка Intern не в архиве и не тимлид (тимлиды — сотрудники,
  как и в недельном отчёте 1.0; ПМ считаются стажёрами).
* Активный стажёр — статус «Активный». Считаются люди, а не участия:
  человек на двух проектах — один активный стажёр.
* Выпускник — только по достоверным событиям (apps.dashboard.graduation):
  был активен в момент официального завершения проекта и не заморожен на эту
  дату, или есть сохранённая отметка выпуска, или повышен до штатного
  тимлида. Повторная стажировка выпуском НЕ считается. Неоднозначные записи
  не считаются и выводятся на сверку.
* Новый стажёр — активный и не выпускник; старый — активный выпускник.
* Основной этап проекта — Project.primary_stage, выбранный вручную
  руководителем или ПМ. Если не выбран — подсказка по current_stage,
  помеченная «не подтверждено». Завершённые по статусу — всегда «Завершён».
* Неделя — понедельник–воскресенье по времени Бишкека (TIME_ZONE).
"""
from __future__ import annotations

import datetime
from collections import Counter, defaultdict
from dataclasses import dataclass

from django.db.models import Count, Q
from django.utils import timezone

from apps.interns.models import GraduateStatus, Intern, InternStatus
from apps.interns.services import BRANCH_BISHKEK, BRANCH_OSH, BRANCH_UNKNOWN, branch_of
from apps.projects.models import Project, ProjectStageKey, ProjectStatus
from apps.teams.models import TeamMember, TeamRole
from apps.teams.selectors import staff_intern_ids

EXPELLED = (TeamMember.LeftReason.NOT_FIT, TeamMember.LeftReason.UNRESPONSIVE, TeamMember.LeftReason.EXPELLED)
SELF_LEFT = (TeamMember.LeftReason.SELF, TeamMember.LeftReason.CANCELLED)
BRANCH_ORDER = (BRANCH_BISHKEK, BRANCH_OSH, BRANCH_UNKNOWN)


# ================================================================ недели

@dataclass(frozen=True)
class Week:
    start: datetime.date
    end: datetime.date
    complete: bool

    @property
    def key(self) -> str:
        return self.start.isoformat()

    @property
    def label(self) -> str:
        text = f'{self.start:%d.%m} – {self.end:%d.%m.%Y}'
        return text if self.complete else f'{text} (неполная)'


def week_of(day: datetime.date, today: datetime.date | None = None) -> Week:
    today = today or timezone.localdate()
    start = day - datetime.timedelta(days=day.weekday())
    end = start + datetime.timedelta(days=6)
    return Week(start, end, complete=end < today)


def last_complete_week(today: datetime.date | None = None) -> Week:
    today = today or timezone.localdate()
    return week_of(today - datetime.timedelta(days=today.weekday() + 7), today)


def recent_weeks(count: int = 8, today: datetime.date | None = None) -> list[Week]:
    """Текущая (неполная) неделя и `count` завершённых до неё — для выбора."""
    today = today or timezone.localdate()
    current = week_of(today, today)
    return [current] + [
        week_of(current.start - datetime.timedelta(weeks=n), today) for n in range(1, count + 1)
    ]


def parse_week(value: str | None, today: datetime.date | None = None) -> Week:
    """Неделя из ?week=YYYY-MM-DD; по умолчанию — последняя завершённая."""
    today = today or timezone.localdate()
    try:
        day = datetime.date.fromisoformat(value or '')
    except ValueError:
        return last_complete_week(today)
    return week_of(min(day, today), today)


# ================================================================ люди

def _interns(staff: set | None = None):
    return Intern.objects.active().exclude(pk__in=staff_intern_ids() if staff is None else staff)


def graduation():
    """Выпускники и записи на сверку (см. apps.dashboard.graduation)."""
    from apps.dashboard.graduation import compute

    return compute()


def graduate_ids() -> set[int]:
    return graduation().ids


def people_kpi(staff: set | None = None, graduates: set | None = None) -> dict:
    """KPI по людям: уникальные люди и, отдельно, число их участий."""
    graduates = graduate_ids() if graduates is None else graduates
    interns = _interns(staff)
    active_ids = set(interns.filter(status=InternStatus.ACTIVE).values_list('pk', flat=True))
    participations = TeamMember.objects.filter(
        intern_id__in=active_ids, status=TeamMember.Status.ACTIVE, project__isnull=False,
    ).count()
    return {
        'active': len(active_ids),
        'new': len(active_ids - graduates),
        'old': len(active_ids & graduates),
        # Выпускники — все люди со статусом выпускника, включая повышенных до
        # тимлида (они выпускники по правилам 2.0), в любом текущем статусе
        'graduates': Intern.objects.active().filter(pk__in=graduates).count(),
        'active_participations': participations,
    }


def branch_table(staff: set | None = None, graduates: set | None = None) -> dict:
    """Активные стажёры по филиалам и направлениям: новые / старые / всего.

    Строится по карточкам людей (одна строка — один человек), поэтому
    участие в нескольких проектах число людей не увеличивает.
    """
    graduates = graduate_ids() if graduates is None else graduates
    rows = _interns(staff).filter(status=InternStatus.ACTIVE).values(
        'pk', 'branch', 'city', 'specialization_id', 'specialization__name',
    )
    cells: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: {'new': 0, 'old': 0, 'total': 0}))
    spec_ids: dict[str, int | None] = {}
    for row in rows:
        branch = branch_of(Intern(branch=row['branch'], city=row['city']))
        spec = row['specialization__name'] or 'Направление не указано'
        spec_ids[spec] = row['specialization_id']
        cell = cells[branch][spec]
        kind = 'old' if row['pk'] in graduates else 'new'
        cell[kind] += 1
        cell['total'] += 1

    branches = []
    grand = Counter()
    for branch in BRANCH_ORDER:
        if branch not in cells and branch == BRANCH_UNKNOWN:
            continue
        specs = cells.get(branch, {})
        lines = [
            {'name': name, 'specialization_id': spec_ids.get(name), **values}
            for name, values in sorted(specs.items(), key=lambda item: (-item[1]['total'], item[0]))
        ]
        totals = Counter()
        for line in lines:
            totals.update({k: line[k] for k in ('new', 'old', 'total')})
        grand.update(totals)
        branches.append({'name': branch, 'lines': lines, 'totals': dict(totals) or {'new': 0, 'old': 0, 'total': 0}})
    return {'branches': branches, 'totals': {'new': grand['new'], 'old': grand['old'], 'total': grand['total']}}


# ================================================================ проекты

STAGE_CATEGORIES = [
    ('new', 'Новый', (
        ProjectStageKey.NEW, ProjectStageKey.DOCUMENTS,
        ProjectStageKey.REQUIREMENTS, ProjectStageKey.TEAM_FORMING,
    )),
    ('development', 'Активная разработка', (
        ProjectStageKey.DESIGN, ProjectStageKey.BACKEND,
        ProjectStageKey.FRONTEND, ProjectStageKey.MOBILE_DEV,
    )),
    ('testing', 'Staging / тестирование', (
        ProjectStageKey.STAGING, ProjectStageKey.TESTING, ProjectStageKey.REWORK,
    )),
    ('delivery', 'Передача заказчику', (ProjectStageKey.DELIVERY, ProjectStageKey.PRODUCTION)),
    ('completed', 'Завершён', (ProjectStageKey.COMPLETED,)),
]
STOPPED_STATUSES = (ProjectStatus.CANCELLED, ProjectStatus.REFUSED)


def _plural(n: int, one: str, few: str, many: str) -> str:
    # «у 1 проекта», «у 3 проектов» — после «у» родительный падеж
    return one if n % 10 == 1 and n % 100 != 11 else many


def stage_category(status: str, stage_key: str | None) -> str | None:
    """Одна категория на проект. Завершённые по статусу — «Завершён» при любом
    выбранном этапе; прекращённые (отменён / отказ заказчика) в распределение
    не входят. Этап «Завершён» у незакрытого проекта — «Передача заказчику»:
    закрытым проект делает только статус."""
    if status == ProjectStatus.COMPLETED:
        return 'completed'
    if status in STOPPED_STATUSES:
        return None
    if stage_key == ProjectStageKey.COMPLETED:
        return 'delivery'
    for key, _label, stages in STAGE_CATEGORIES:
        if stage_key in stages:
            return key
    return 'new'


def project_kpi() -> dict:
    counts = dict(
        Project.objects.active().values_list('status').annotate(n=Count('pk')).values_list('status', 'n'),
    )
    return {
        'total': sum(counts.values()),
        'active': counts.get(ProjectStatus.ACTIVE, 0),
        'paused': counts.get(ProjectStatus.PAUSED, 0),
        'completed': counts.get(ProjectStatus.COMPLETED, 0),
        'stopped': sum(counts.get(s, 0) for s in STOPPED_STATUSES),
    }


def projects_by_stage() -> dict:
    """Распределение проектов по основному этапу — каждый проект ровно в одной
    категории. Проект без выбранного основного этапа учитывается по подсказке
    (current_stage) и отдельно считается как «не подтверждено»."""
    labels = dict(ProjectStageKey.choices)
    rows = Project.objects.active().values('status', 'primary_stage__key', 'current_stage').annotate(n=Count('pk'))
    categories = {
        key: {'key': key, 'label': label, 'count': 0, 'unconfirmed': 0, 'stages': Counter()}
        for key, label, _stages in STAGE_CATEGORIES
    }
    stopped = unconfirmed = 0
    for row in rows:
        confirmed = bool(row['primary_stage__key'])
        stage_key = row['primary_stage__key'] or row['current_stage']
        category = stage_category(row['status'], stage_key)
        if category is None:
            stopped += row['n']
            continue
        categories[category]['count'] += row['n']
        if category == 'completed':
            continue  # завершённые считаются по статусу, этап не важен
        if not confirmed:
            categories[category]['unconfirmed'] += row['n']
            unconfirmed += row['n']
        categories[category]['stages'][stage_key] += row['n']
    total = sum(c['count'] for c in categories.values())
    result = []
    for category in categories.values():
        category['share'] = round(category['count'] / total * 100, 1) if total else 0
        category['stages'] = [
            {'key': key, 'label': labels.get(key, key), 'count': n}
            for key, n in sorted(category['stages'].items(), key=lambda item: -item[1])
        ]
        result.append(category)
    return {'categories': result, 'total': total, 'stopped': stopped, 'unconfirmed': unconfirmed,
            'unconfirmed_label': f'{unconfirmed} {_plural(unconfirmed, "проекта", "проектов", "проектов")}'}


# ================================================================ неделя: события

def _left_people(week: Week, reasons, staff: set, period_reason: str = '') -> int:
    """Люди, ушедшие за неделю по этим причинам. С UI-04B отчисление и отмена
    — ещё и закрытый период стажировки (у человека могло не быть участий).
    Предупреждение и решение «Оставить» сюда не попадают."""
    from apps.interns.models import Internship

    people = set(TeamMember.objects.filter(
        intern__isnull=False, left_reason__in=reasons,
        left_at__gte=week.start, left_at__lte=week.end,
    ).exclude(intern_id__in=staff).values_list('intern_id', flat=True))
    if period_reason:
        people |= set(Internship.objects.filter(
            end_reason=period_reason, ended_on__gte=week.start, ended_on__lte=week.end,
        ).exclude(intern_id__in=staff).values_list('intern_id', flat=True))
    return len(people)


def _freezes(week: Week) -> int:
    from apps.audit.models import AuditLog

    return AuditLog.objects.filter(
        object_type='Intern', created_at__date__gte=week.start, created_at__date__lte=week.end,
    ).filter(
        Q(action='Стажировка заморожена') | Q(action='Статус изменён', new_value=InternStatus.PAUSED.label),
    ).values('object_id').distinct().count()


DIRECTION_ROLES = (TeamRole.BACKEND, TeamRole.FRONTEND, TeamRole.MOBILE, TeamRole.UXUI, TeamRole.QA)


def meeting_obligations(week: Week) -> int:
    """Обязательные собрания недели по правилу 2.0: минимум одно на каждое
    активное направление каждого идущего проекта — даже если встречу не
    создали. Направление активно, если в нём был участник-стажёр."""
    return TeamMember.objects.filter(
        intern__isnull=False, role__in=DIRECTION_ROLES, project__isnull=False,
        project__is_archived=False, project__status__in=[ProjectStatus.ACTIVE, ProjectStatus.PAUSED],
    ).filter(Q(joined_at__isnull=True) | Q(joined_at__lte=week.end)).filter(
        Q(status=TeamMember.Status.ACTIVE) | Q(left_at__gte=week.start),
    ).values('project_id', 'role').distinct().count()


def _meetings(week: Week, today: datetime.date) -> dict:
    """Собрания по записям табеля (это не то же, что обязательства: признака
    «обязательное» у записи нет, а пропущенное обязательство может вообще
    не иметь записи — см. docs/meetings-schema.md)."""
    from apps.attendance.models import GroupMeeting, MeetingKind

    meetings = GroupMeeting.objects.filter(date__gte=week.start, date__lte=week.end).exclude(kind=MeetingKind.OTHER)
    by_status = dict(meetings.values_list('status').annotate(n=Count('pk')).values_list('status', 'n'))
    overdue_unmarked = meetings.filter(status=GroupMeeting.Status.PLANNED, date__lt=today).count()
    upcoming = meetings.filter(status=GroupMeeting.Status.PLANNED, date__gte=today).count()
    from apps.video.obligations import summary

    video = summary(week, today)  # этап 9B: обязательные видеособрания
    return {
        'held': by_status.get(GroupMeeting.Status.HELD, 0),
        'missed': by_status.get(GroupMeeting.Status.MISSED, 0) + overdue_unmarked,
        'missed_marked': by_status.get(GroupMeeting.Status.MISSED, 0),
        'missed_unmarked': overdue_unmarked,
        'upcoming': upcoming,
        'obligations': video['obligations'],
        'obligations_missed': video['missed'],  # None — неделя не закончилась
        'obligations_reason': '' if video['complete'] else 'неделя не закончилась — пропуск не ставится',
        'video': video,
    }


def pm_report_obligations(week: Week) -> list[dict]:
    """Кто из ПМ должен был сдать недельный отчёт: ПМ, у которого на этой
    неделе был хотя бы один идущий проект (участие и проект — по датам)."""
    members = TeamMember.objects.filter(
        role=TeamRole.PROJECT_MANAGER, intern__user__isnull=False, project__isnull=False,
        project__is_archived=False,
    ).filter(
        Q(joined_at__isnull=True) | Q(joined_at__lte=week.end),
    ).filter(
        Q(status=TeamMember.Status.ACTIVE) | Q(left_at__gte=week.start),
    ).filter(
        Q(project__status__in=[ProjectStatus.ACTIVE, ProjectStatus.PAUSED])
        | Q(project__status=ProjectStatus.COMPLETED, project__actual_end_date__gte=week.start),
    ).select_related('intern', 'project')
    pms: dict[int, dict] = {}
    for member in members:
        entry = pms.setdefault(member.intern.user_id, {
            'user_id': member.intern.user_id, 'name': member.intern.full_name,
            'intern_id': member.intern_id, 'projects': [],
        })
        entry['projects'].append(member.project)
    return list(pms.values())


def pm_reports(week: Week, today: datetime.date) -> dict:
    """Недельные отчёты ПМ: один отчёт в неделю на ПМ, срок — пятница или
    суббота. Считаются отчёты (по одному на ПМ), а не люди в штате.

    Источники (этап 7B): сводный отчёт ПМ (reports.PmWeeklyReport — в срок
    по фактической дате подачи) и, как раньше, отчёты по проекту
    (ProjectReport) с датой в эту неделю — чтобы прошлые недели не потеряли
    уже сданные отчёты."""
    from apps.projects.models import ProjectReport
    from apps.reports.models import PmWeeklyReport

    obligations = pm_report_obligations(week)
    expected = {o['user_id'] for o in obligations}
    days = defaultdict(set)
    for author_id, day in ProjectReport.objects.filter(
        author_id__in=expected, date__gte=week.start, date__lte=week.end,
    ).values_list('author_id', 'date'):
        days[author_id].add(day)
    on_time = {uid for uid, ds in days.items() if any(d.weekday() in (4, 5) for d in ds)}
    by_intern = {o['intern_id']: o['user_id'] for o in obligations}
    for pm_id, timely in PmWeeklyReport.objects.filter(
        pm_id__in=by_intern, week_start=week.start,
    ).values_list('pm_id', 'on_time'):
        days[by_intern[pm_id]].add(None)
        if timely:
            on_time.add(by_intern[pm_id])
    other_day = set(days) - on_time
    deadline = week.start + datetime.timedelta(days=5)  # суббота
    due = deadline < today
    missing = expected - on_time - other_day
    return {
        'expected': len(expected),
        'on_time': len(on_time),
        'other_day': len(other_day),
        'missing': len(missing) if due else None,
        'pending': len(missing) if not due else 0,
        'due': due,
        'missing_people': [o for o in obligations if o['user_id'] in missing] if due else [],
    }


def lead_reports(week: Week, today: datetime.date | None = None) -> dict:
    """Отчёты тимлидов (этап 7B): минимум один обязательный в неделю на
    каждый закреплённый проект и направление. «Не сдано» — только за
    завершённую неделю; до конца недели отчёт «ожидается»."""
    from apps.reports.weekly import lead_summary

    data = lead_summary(week, today)
    data['reason'] = '' if data['due'] else 'неделя не закончилась — пропуск не ставится'
    return data


def weekly_activity(week: Week, today: datetime.date | None = None, staff: set | None = None, grad=None) -> dict:
    """События недели — только по фактическим датам событий."""
    today = today or timezone.localdate()
    first_graduation = (grad or graduation()).first_dates
    staff = staff_intern_ids() if staff is None else staff
    return {
        'week': week,
        'registered': _interns(staff).filter(
            created_at__date__gte=week.start, created_at__date__lte=week.end,
        ).count(),
        # Первая датированная отметка выпуска; повышение до тимлида тоже выпуск
        'graduated': sum(1 for day in first_graduation.values() if week.start <= day <= week.end),
        'frozen': _freezes(week),
        'self_left': _left_people(week, SELF_LEFT, staff, 'cancelled'),
        'expelled': _left_people(week, EXPELLED, staff, 'expelled'),
        'projects_completed': Project.objects.active().filter(
            status=ProjectStatus.COMPLETED, actual_end_date__gte=week.start, actual_end_date__lte=week.end,
        ).count(),
        'meetings': _meetings(week, today),
        'pm_reports': pm_reports(week, today),
        'lead_reports': lead_reports(week, today),
    }


# ================================================================ сравнение недель

# (ключ, подпись, вид). event — считается по датам событий за любую неделю;
# state — численность на конец недели, берётся только из сохранённого снимка.
COMPARISON_ROWS = [
    ('registered', 'Зарегистрировано новых стажёров', 'event'),
    ('graduated', 'Получили статус выпускника', 'event'),
    ('frozen', 'Заморозки', 'event'),
    ('self_left', 'Добровольные отмены', 'event'),
    ('expelled', 'Отчисления', 'event'),
    ('projects_completed', 'Завершено проектов', 'event'),
    ('meet_obligations', 'Обязательных собраний (направлений)', 'event'),
    ('meet_fulfilled', 'Обязательные собрания проведены', 'event'),
    ('meet_missed', 'Обязательные собрания пропущены', 'event'),
    ('meet_insufficient', 'Собрания: недостаточно данных', 'event'),
    ('meet_unique', 'Уникальных участников собраний (подтверждено)', 'event'),
    ('meetings_held', 'Собраний проведено (по табелю)', 'event'),
    ('meetings_missed', 'Собраний не проведено (по табелю)', 'event'),
    ('pm_on_time', 'Отчёты ПМ сданы (пт/сб)', 'event'),
    ('pm_late', 'Отчёты ПМ не в срок', 'event'),
    ('pm_missing', 'Отчёты ПМ не сданы', 'event'),
    ('lead_expected', 'Обязательных отчётов тимлидов', 'event'),
    ('lead_submitted', 'Отчёты тимлидов сданы', 'event'),
    ('lead_missing', 'Отчёты тимлидов не сданы', 'event'),
    ('active_interns', 'Активные стажёры — на конец недели', 'state'),
    ('new_interns', 'Новые стажёры — на конец недели', 'state'),
    ('old_interns', 'Старые стажёры — на конец недели', 'state'),
    ('graduates_total', 'Выпускники — на конец недели', 'state'),
    ('active_projects', 'Активные проекты — на конец недели', 'state'),
]


def _event_values(week: Week, today: datetime.date, staff: set, grad) -> dict:
    data = weekly_activity(week, today, staff, grad)
    return {
        'registered': data['registered'], 'graduated': data['graduated'], 'frozen': data['frozen'],
        'self_left': data['self_left'], 'expelled': data['expelled'],
        'projects_completed': data['projects_completed'],
        'meetings_held': data['meetings']['held'], 'meetings_missed': data['meetings']['missed'],
        'meet_obligations': data['meetings']['video']['obligations'],
        'meet_fulfilled': data['meetings']['video']['fulfilled'],
        'meet_missed': data['meetings']['video']['missed'],
        'meet_insufficient': data['meetings']['video']['insufficient'],
        'meet_unique': data['meetings']['video']['unique_participants'],
        'pm_on_time': data['pm_reports']['on_time'], 'pm_missing': data['pm_reports']['missing'],
        'pm_late': data['pm_reports']['other_day'],
        'lead_expected': data['lead_reports']['expected'], 'lead_submitted': data['lead_reports']['submitted'],
        'lead_missing': data['lead_reports']['missing'],
    }


def state_snapshot() -> dict:
    """Численность сейчас — сохраняется в недельный снимок (WeeklyReport.data),
    чтобы потом сравнивать недели. Ключи совпадают с COMPARISON_ROWS."""
    people = people_kpi()
    return {
        'active_interns': people['active'], 'new_interns': people['new'],
        'old_interns': people['old'], 'graduates_total': people['graduates'],
        'active_projects': project_kpi()['active'],
    }


def _state_values(week: Week) -> dict:
    """Численность на конец недели — только из снимка этой недели."""
    from apps.reports.models import WeeklyReport

    report = WeeklyReport.objects.filter(week_start=week.start).first()
    if report is None:
        return {}
    data = report.data or {}
    values = dict(data.get('dashboard_state') or {})
    # Снимки 1.0: те же определения у активных стажёров и проектов
    values.setdefault('active_interns', (data.get('interns') or {}).get('active'))
    values.setdefault('active_projects', (data.get('projects') or {}).get('active'))
    return {k: v for k, v in values.items() if v is not None}


def compare_weeks(week: Week, today: datetime.date | None = None) -> dict:
    today = today or timezone.localdate()
    previous = week_of(week.start - datetime.timedelta(days=7), today)
    staff, grad = staff_intern_ids(), graduation()
    current_events, previous_events = _event_values(week, today, staff, grad), _event_values(previous, today, staff, grad)
    current_state, previous_state = _state_values(week), _state_values(previous)
    rows = []
    for key, label, kind in COMPARISON_ROWS:
        a = (current_events if kind == 'event' else current_state).get(key)
        b = (previous_events if kind == 'event' else previous_state).get(key)
        delta = a - b if a is not None and b is not None else None
        percent = round(delta / b * 100, 1) if delta is not None and b else None
        rows.append({'key': key, 'label': label, 'kind': kind, 'current': a, 'previous': b,
                     'delta': delta, 'percent': percent})
    return {'week': week, 'previous': previous, 'rows': rows,
            'has_state': bool(current_state or previous_state)}

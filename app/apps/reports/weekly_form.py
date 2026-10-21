"""Недельный отчёт GeeksPro — по форме рабочей таблицы (лист «отчеты»).

Часть показателей считается «за неделю» (по датам событий), часть —
«на конец недели» (текущее состояние). В форме это подписано явно,
чтобы цифры не путались между собой.

Если показатель нельзя посчитать, потому что исходное поле нигде
не заполнено, вместо нуля показывается прочерк и подсказка, что
именно надо заполнить — ноль и «нет данных» это разные вещи.
"""
import datetime

from apps.attendance.models import Attendance, GroupMeeting, MeetingKind
from apps.interns.models import Intern, InternStatus, WORKING_STATUSES
from apps.projects.models import Project, ProjectStatus, ProjectStatusHistory

# Норматив сдачи проекта: 110 дней с даты подписания договора
DELIVERY_LIMIT_DAYS = 110

# Как статусы проекта выглядят в истории изменений
CANCELLED_LABEL = ProjectStatus.CANCELLED.label
REFUSED_LABEL = ProjectStatus.REFUSED.label


def week_bounds(day: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Понедельник — воскресенье недели, в которую попадает дата."""
    start = day - datetime.timedelta(days=day.weekday())
    return start, start + datetime.timedelta(days=6)


def _status_changes(first: datetime.date, last: datetime.date, label: str) -> int:
    """Сколько проектов за неделю перешли в указанный статус."""
    return ProjectStatusHistory.objects.filter(
        field='Статус', new_value=label,
        created_at__date__gte=first, created_at__date__lte=last,
    ).values('project').distinct().count()


def build(week_start: datetime.date) -> dict:
    """Считает все показатели недельного отчёта."""
    first, last = week_bounds(week_start)
    projects = Project.objects.active()
    interns = Intern.objects.active()

    # --- Проекты в разработке ---
    active_projects = projects.filter(status=ProjectStatus.ACTIVE).count()
    signed_contracts = projects.filter(
        contract_date__gte=first, contract_date__lte=last,
    ).count()
    new_projects = projects.filter(
        created_at__date__gte=first, created_at__date__lte=last,
    ).count()

    # --- Принятые заказчиком за неделю ---
    completed = list(
        projects.filter(
            status=ProjectStatus.COMPLETED,
            actual_end_date__gte=first, actual_end_date__lte=last,
        ),
    )
    accepted_in_time = sum(
        1 for project in completed
        if project.contract_date
        and (project.actual_end_date - project.contract_date).days <= DELIVERY_LIMIT_DAYS
    )
    accepted_late = sum(
        1 for project in completed
        if project.planned_end_date
        and project.actual_end_date > project.planned_end_date
    )

    # --- Незавершённые: по факту смены статуса на этой неделе ---
    stopped_by_us = _status_changes(first, last, CANCELLED_LABEL)
    stopped_by_client = _status_changes(first, last, REFUSED_LABEL)
    problematic_projects = projects.filter(is_problematic=True).count()

    # --- Стажёры (тимлиды — сотрудники, в счёт не идут; ПМ идут) ---
    from apps.teams.selectors import staff_intern_ids

    leads = staff_intern_ids()
    active_interns = (
        interns.filter(status__in=WORKING_STATUSES).exclude(pk__in=leads).count()
    )
    new_interns = interns.filter(
        internship_start_date__gte=first, internship_start_date__lte=last,
    ).count()

    # --- Выпускники (состояние на конец недели) ---
    finished = interns.filter(
        status__in=[InternStatus.EMPLOYABLE, InternStatus.EMPLOYED],
    ).count()
    employed = interns.filter(status=InternStatus.EMPLOYED).count()
    resume_bank_rate = round(employed / finished * 100) if finished else None

    # --- Выбывшие (состояние на конец недели) ---
    dropped = interns.filter(status=InternStatus.DROPPED).count()
    paused = interns.filter(status=InternStatus.PAUSED).count()

    # --- Внутренние собрания за неделю ---
    meetings = GroupMeeting.objects.filter(
        kind=MeetingKind.INTERNAL, date__gte=first, date__lte=last,
    )
    planned_meetings = meetings.count()
    held_meetings = meetings.filter(status=GroupMeeting.Status.HELD).count()
    missed_meetings = planned_meetings - held_meetings

    marks = list(Attendance.objects.filter(meeting__in=meetings))
    attended = sum(1 for mark in marks if mark.is_attended)
    attendance_rate = round(attended / len(marks) * 100) if marks else None
    absence_rate = 100 - attendance_rate if attendance_rate is not None else None

    return {
        'period': {'start': first.isoformat(), 'end': last.isoformat()},
        'projects': {
            'active': active_projects,
            'signed_contracts': signed_contracts,
            'new': new_projects,
        },
        'accepted': {
            'in_time': accepted_in_time,
            'late': accepted_late,
        },
        'stopped': {
            'by_us': stopped_by_us,
            'by_client': stopped_by_client,
            'problematic': problematic_projects,
        },
        'interns': {
            'active': active_interns,
            'new': new_interns,
        },
        'graduates': {
            'finished': finished,
            'resume_bank_rate': resume_bank_rate,
        },
        'left': {
            'dropped': dropped,
            'paused': paused,
        },
        'meetings': {
            'planned': planned_meetings,
            'held': held_meetings,
            'missed': missed_meetings,
            'attendance_rate': attendance_rate,
            'absence_rate': absence_rate,
        },
        'gaps': gaps(),
    }


def gaps() -> dict:
    """Показатели, которые нельзя посчитать — исходные поля не заполнены."""
    result = {}
    if not Project.objects.active().exclude(contract_date__isnull=True).exists():
        result['contract_date'] = 'Ни у одного проекта не заполнена дата договора'
    if not Project.objects.active().exclude(actual_end_date__isnull=True).exists():
        result['actual_end_date'] = (
            'Ни один проект ещё не завершён — нет фактической даты сдачи'
        )
    if not Intern.objects.active().exclude(
        internship_start_date__isnull=True,
    ).exists():
        result['internship_start'] = (
            'У стажёров не заполнена дата начала стажировки'
        )
    if not Intern.objects.active().filter(
        status__in=[InternStatus.EMPLOYABLE, InternStatus.EMPLOYED],
    ).exists():
        result['graduates'] = 'Никому не проставлен статус выпускника'
    if not GroupMeeting.objects.filter(kind=MeetingKind.INTERNAL).exists():
        result['meetings'] = 'Внутренние собрания ещё не создавались в табеле'
    return result


# Блок → строки (подпись, ключ, суффикс, ключ подсказки о нехватке данных)
SECTIONS = [
    ('Проекты в разработке', 'projects', [
        ('Активных проектов — на конец недели', 'active', '', None),
        ('Новые проекты за неделю', 'new', '', None),
        ('Подписано договоров за неделю', 'signed_contracts', '', 'contract_date'),
    ]),
    ('Принято заказчиком за неделю', 'accepted', [
        (f'Принято в срок — до {DELIVERY_LIMIT_DAYS} дней с договора',
         'in_time', '', 'actual_end_date'),
        ('Передано с просрочкой', 'late', '', 'actual_end_date'),
    ]),
    ('Незавершённые проекты за неделю', 'stopped', [
        ('Прекращены по инициативе исполнителя', 'by_us', '', None),
        ('Прекращены по инициативе заказчика', 'by_client', '', None),
        ('Проблемные проекты', 'problematic', '', None),
    ]),
    ('Стажёры', 'interns', [
        ('Активных стажёров — на конец недели', 'active', '', None),
        ('Вышли на стажировку за неделю', 'new', '', 'internship_start'),
    ]),
    ('Выпускники — на конец недели', 'graduates', [
        ('Успешно завершившие стажировку', 'finished', '', 'graduates'),
        ('% добавленных в банк резюме', 'resume_bank_rate', '%', 'graduates'),
    ]),
    ('Выбывшие из стажировки — на конец недели', 'left', [
        ('Отчисленные', 'dropped', '', None),
        ('Приостановившие', 'paused', '', None),
    ]),
    ('Внутренние собрания за неделю', 'meetings', [
        ('Запланированные', 'planned', '', 'meetings'),
        ('Проведённые', 'held', '', 'meetings'),
        ('Непроведённые', 'missed', '', 'meetings'),
        ('% посещаемости собраний', 'attendance_rate', '%', 'meetings'),
        ('% неприсутствия', 'absence_rate', '%', 'meetings'),
    ]),
]


def as_sections(data: dict) -> list[dict]:
    """Готовит данные отчёта к выводу блоками."""
    missing = data.get('gaps', {})
    result = []
    for title, group_key, rows in SECTIONS:
        group = data.get(group_key, {})
        prepared = []
        for label, key, suffix, gap_key in rows:
            hint = missing.get(gap_key) if gap_key else None
            prepared.append({
                'label': label,
                'value': group.get(key),
                'suffix': suffix,
                'hint': hint,
                'no_data': bool(hint),
            })
        result.append({'title': title, 'rows': prepared})
    return result

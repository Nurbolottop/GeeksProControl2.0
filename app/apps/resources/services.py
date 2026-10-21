"""ResourcePlanningService (ТЗ §14): баланс людей по направлениям."""
import datetime

from django.utils import timezone

from apps.interns.models import (
    AVAILABLE_STATUSES, WORKING_STATUSES, Intern, InternStatus,
)
from apps.resources.models import PlannedProject, PlannedProjectNeed
from apps.teams.models import TeamMember
from apps.training.models import Specialization, TrainingGroup

# Горизонт прогноза выпусков, месяцев
FORECAST_MONTHS = 3


def _people_for_summary():
    """Стажёры, которых считаем: без архивных, выбывших и тимлидов.

    Тимлиды — сотрудники и в эти цифры не входят, а ПМ считаются
    стажёрами наравне с остальными.
    """
    from apps.teams.selectors import staff_intern_ids

    staff = staff_intern_ids()
    people = list(
        Intern.objects.active()
        .exclude(status=InternStatus.DROPPED)
        .exclude(pk__in=staff)
        .select_related('specialization'),
    )
    busy_ids = set(
        TeamMember.objects.filter(
            status=TeamMember.Status.ACTIVE, intern__isnull=False,
        ).values_list('intern_id', flat=True),
    )
    return people, busy_ids, staff


def _counts(people, busy_ids) -> dict:
    """Разрез по одной группе людей: сколько всего и кто в каком состоянии.

    «Выпускники» — те, у кого стоит статус выпускника (вышли с завершённого
    проекта и ещё не разобраны), «заморозка» — стажировка приостановлена,
    «ожидают» — ещё не начали стажировку, «свободные» — кого можно занять
    прямо сейчас: без проекта, в рабочем статусе и либо не выпускник, либо
    уже сказавший через бота, что продолжает и ждёт проект.
    """
    from apps.interns.models import GraduateStatus

    busy = sum(1 for person in people if person.pk in busy_ids)
    free = sum(
        1 for p in people
        if p.pk not in busy_ids
        and p.status in AVAILABLE_STATUSES
        and (not p.graduate_status or p.graduate_status == GraduateStatus.WAITING)
    )
    return {
        'total': len(people),
        'active': sum(1 for p in people if p.status == InternStatus.ACTIVE),
        'paused': sum(1 for p in people if p.status == InternStatus.PAUSED),
        'waiting': sum(1 for p in people if p.status == InternStatus.WAITING),
        'graduates': sum(1 for p in people if p.graduate_status),
        'busy': busy,
        'free': free,
    }


def interns_summary() -> list[dict]:
    """По направлениям: всего, активные, заморозка, ожидают, выпускники, свободные.

    Тимлиды сюда не входят — они сотрудники; ПМ считаются стажёрами.
    """
    people, busy_ids, _ = _people_for_summary()
    by_spec = {}
    for person in people:
        by_spec.setdefault(person.specialization_id, []).append(person)

    rows = []
    for spec in Specialization.objects.all():
        own = by_spec.get(spec.pk, [])
        rows.append({'specialization': spec, **_counts(own, busy_ids)})
    without_spec = by_spec.get(None, [])
    if without_spec:
        rows.append({
            'specialization': None, 'is_without_spec': True,
            **_counts(without_spec, busy_ids),
        })
    rows.sort(key=lambda row: (row.get('is_without_spec', False), -row['total']))
    return rows


def interns_total() -> dict:
    """Общий итог по стажёрам — по людям, а не сложением направлений:
    человек без направления тоже попадает в общее число."""
    people, busy_ids, staff = _people_for_summary()
    return {
        **_counts(people, busy_ids),
        'without_spec': sum(1 for p in people if p.specialization_id is None),
        'leads': len(staff),
    }


def resource_balance(today: datetime.date | None = None) -> list[dict]:
    """Таблица баланса: Направление | Доступно | Выпуск | Нужно | Баланс.

    - Доступно: стажёры в рабочих статусах без активного проекта
      + «готов к распределению».
    - Выпуск: прогноз перехода в стажировку из групп, заканчивающихся
      в ближайшие FORECAST_MONTHS месяцев.
    - Нужно: потребность планируемых проектов в активных статусах.
    """
    today = today or timezone.localdate()
    horizon = today + datetime.timedelta(days=30 * FORECAST_MONTHS)

    busy_ids = set(
        TeamMember.objects.filter(
            status=TeamMember.Status.ACTIVE, intern__isnull=False,
        ).values_list('intern_id', flat=True),
    )

    rows = []
    for spec in Specialization.objects.all():
        available = (
            Intern.objects.active()
            .filter(
                specialization=spec,
                status__in=[*WORKING_STATUSES, InternStatus.READY],
            )
            .exclude(pk__in=busy_ids)
            .count()
        )
        graduating = sum(
            group.expected_interns
            for group in TrainingGroup.objects.filter(
                specialization=spec,
                end_date__gte=today, end_date__lte=horizon,
            )
        )
        needed = sum(
            need.count
            for need in PlannedProjectNeed.objects.filter(
                specialization=spec,
                planned_project__status__in=PlannedProject.ACTIVE_STATUSES,
            )
        )
        balance = available + graduating - needed
        rows.append({
            'specialization': spec,
            'available': available,
            'graduating': graduating,
            'needed': needed,
            'balance': balance,
            'deficit': balance < 0,
        })
    return rows


def upcoming_graduations(today: datetime.date | None = None) -> list[TrainingGroup]:
    """Будущие выпуски по месяцам (ТЗ §13)."""
    today = today or timezone.localdate()
    return list(
        TrainingGroup.objects.filter(end_date__gte=today)
        .select_related('specialization')
        .order_by('end_date'),
    )

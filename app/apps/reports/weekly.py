"""Еженедельная отчётность (этап 7B): отчёты тимлидов, сводные отчёты ПМ,
еженедельные оценки стажёров.

Правила:
* неделя — понедельник–воскресенье по Бишкеку (apps.dashboard.metrics.week_of);
* тимлид — минимум один обязательный отчёт в неделю по каждому
  закреплённому проекту и направлению; в любой день недели; без
  утверждения; дополнительные — отдельно, в счёт обязательного не идут;
* обязательство тимлида: «сдан» / «ожидается» (неделя не закончилась) /
  «не сдан» (только за завершённую неделю — пропуск заранее не ставится);
* ПМ — один сводный отчёт в неделю; в срок — пятница или суббота
  отчётной недели; поданный в другой день сохраняется с фактической
  датой, но в срок не считается; «не сдан» — только после субботы;
* оценка 1–10 — ставит активный тимлид проекта стажёру своего направления,
  активному участнику этого проекта; за текущую или прошлую неделю;
  чужую оценку тимлид не меняет; ПМ оценок и отчётов тимлида не создаёт.
Права проверяются здесь (сервер), повтор и гонка — ограничения в БД.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.dashboard.metrics import Week, week_of
from apps.projects.models import Project, ProjectStatus
from apps.reports.models import LeadWeeklyReport, PmWeeklyReport, WeeklyEvaluation
from apps.teams.models import TeamMember, TeamRole

WRITABLE = (ProjectStatus.ACTIVE, ProjectStatus.PAUSED)
ON_TIME_WEEKDAYS = (4, 5)  # пятница, суббота


class ReportError(ValidationError):
    pass


def _today(today=None):
    return today or timezone.localdate()


def allowed_weeks(today=None) -> list[Week]:
    """Текущая неделя и прошлая (досдать с фактической датой)."""
    today = _today(today)
    current = week_of(today, today)
    return [current, week_of(current.start - datetime.timedelta(days=7), today)]


def _week(week_start, today) -> Week:
    weeks = allowed_weeks(today)
    if week_start in (None, ''):
        return weeks[0]
    if isinstance(week_start, str):
        try:
            week_start = datetime.date.fromisoformat(week_start)
        except ValueError:
            raise ReportError('Неверная неделя.') from None
    for week in weeks:
        if week.start == week_start:
            return week
    raise ReportError('Отчёт и оценку можно сдать только за текущую или прошлую неделю.')


def _profile(user):
    intern = getattr(user, 'intern_profile', None) if user and user.is_authenticated else None
    if intern is None:
        raise PermissionDenied('Нет профиля сотрудника.')
    return intern


def lead_direction(intern) -> str:
    from apps.teams.forms import ROLE_BY_SPECIALIZATION

    spec = intern.specialization.name if intern.specialization_id else ''
    return ROLE_BY_SPECIALIZATION.get(spec, '')


def _active_lead(intern, project):
    return TeamMember.objects.filter(project=project, intern=intern, role=TeamRole.TEAM_LEAD,
                                     status=TeamMember.Status.ACTIVE).exists()


def lead_directions(intern, project) -> list[str]:
    """Направления, которые тимлид сейчас ведёт в проекте (этап 8B: их может быть два)."""
    from apps.teams.staff import lead_member_direction

    members = TeamMember.objects.filter(project=project, intern=intern, role=TeamRole.TEAM_LEAD,
                                        status=TeamMember.Status.ACTIVE).select_related('intern__specialization')
    return sorted({lead_member_direction(m) for m in members} - {''})


# =============================================================== тимлид

def lead_assignments(intern):
    """Закреплённые проекты тимлида, по которым сейчас нужен отчёт."""
    return (TeamMember.objects.filter(intern=intern, role=TeamRole.TEAM_LEAD, status=TeamMember.Status.ACTIVE,
                                      project__isnull=False, project__status__in=WRITABLE)
            .select_related('project').order_by('project__name'))


def submit_lead_report(user, project, text, *, kind=LeadWeeklyReport.Kind.WEEKLY, week_start=None, today=None,
                       direction=None):
    today = _today(today)
    lead = _profile(user)
    text = (text or '').strip()
    if not text:
        raise ReportError('Напишите отчёт.')
    if kind not in LeadWeeklyReport.Kind.values:
        raise ReportError('Неизвестный вид отчёта.')
    if project.status not in WRITABLE or not _active_lead(lead, project):
        raise PermissionDenied('Отчёт сдаёт активный тимлид этого проекта.')
    week = _week(week_start, today)
    own = lead_directions(lead, project) or [lead_direction(lead)]
    if direction in (None, ''):
        if len(own) > 1:
            raise ReportError('Выберите направление: вы ведёте в проекте несколько.')
        direction = own[0]
    elif direction not in own:
        raise PermissionDenied('Это направление в проекте ведёт не вы.')
    try:
        with transaction.atomic():
            return LeadWeeklyReport.objects.create(lead=lead, author=user, project=project, direction=direction,
                                                   week_start=week.start, kind=kind, text=text[:20000])
    except IntegrityError:
        raise ReportError('Обязательный отчёт за эту неделю уже сдан — отправьте дополнительный.') from None


@dataclass
class Obligation:
    lead_id: int
    lead_name: str
    project: Project
    direction: str
    week: Week
    status: str  # submitted / pending / missed
    report: LeadWeeklyReport | None = None

    @property
    def label(self):
        return {'submitted': 'Сдан', 'pending': 'Ожидается', 'missed': 'Не сдан'}[self.status]

    @property
    def tone(self):
        return {'submitted': 'success', 'pending': 'neutral', 'missed': 'danger'}[self.status]


def members_during(week: Week, *, roles=None):
    """Участия, действовавшие хотя бы день этой недели, в проектах, которые в
    эту неделю шли (по истории назначений, а не по сегодняшнему составу).
    Общая основа обязанностей по отчётам (7B) и собраниям (9B)."""
    members = (TeamMember.objects.filter(intern__isnull=False, project__isnull=False, project__is_archived=False)
               .filter(Q(joined_at__isnull=True) | Q(joined_at__lte=week.end))
               .filter(Q(status=TeamMember.Status.ACTIVE) | Q(left_at__gte=week.start))
               .filter(Q(project__status__in=WRITABLE)
                       | Q(project__status=ProjectStatus.COMPLETED, project__actual_end_date__gte=week.start)))
    if roles is not None:
        members = members.filter(role__in=roles)
    return members


def lead_obligations(week: Week, today=None, *, project_ids=None, lead=None, direction='') -> list[Obligation]:
    """Обязательные отчёты тимлидов за неделю: по каждому закреплённому
    (в эту неделю) проекту и направлению — по истории назначений."""
    from apps.dashboard.metrics import week_of as _w

    today = _today(today)
    week = _w(week.start, today)  # полнота недели — на сегодня
    members = (members_during(week, roles=[TeamRole.TEAM_LEAD])
               .select_related('intern__specialization', 'project').order_by('project__name', 'intern__full_name'))
    if project_ids is not None:
        members = members.filter(project_id__in=project_ids)
    if lead is not None:
        members = members.filter(intern=lead)
    from apps.teams.staff import lead_member_direction

    reports = {}
    for report in LeadWeeklyReport.objects.filter(week_start=week.start, kind=LeadWeeklyReport.Kind.WEEKLY):
        reports[(report.lead_id, report.project_id, report.direction)] = report
    seen, result = set(), []
    for member in members:
        own = lead_member_direction(member)
        key = (member.intern_id, member.project_id, own)  # обязанность — пара «проект + направление»
        if key in seen:
            continue
        seen.add(key)
        if direction and own != direction:
            continue
        report = reports.get(key)
        status = 'submitted' if report else ('missed' if week.complete else 'pending')
        result.append(Obligation(member.intern_id, member.intern.full_name, member.project, own, week, status,
                                 report))
    return result


def lead_summary(week: Week, today=None) -> dict:
    items = lead_obligations(week, today)
    week = items[0].week if items else week_of(week.start, _today(today))
    submitted = sum(1 for o in items if o.status == 'submitted')
    return {
        'expected': len(items), 'submitted': submitted,
        'missing': sum(1 for o in items if o.status == 'missed') if week.complete else None,
        'pending': sum(1 for o in items if o.status == 'pending'),
        'due': week.complete,
        'extra': LeadWeeklyReport.objects.filter(week_start=week.start, kind=LeadWeeklyReport.Kind.EXTRA).count(),
        'missing_items': [o for o in items if o.status == 'missed'],
    }


# ================================================================== ПМ

def pm_projects_now(intern):
    return (Project.objects.filter(team_members__intern=intern, team_members__role=TeamRole.PROJECT_MANAGER,
                                   team_members__status=TeamMember.Status.ACTIVE, status__in=WRITABLE)
            .distinct().order_by('name'))


def submit_pm_report(user, text, *, project_ids=(), week_start=None, today=None) -> PmWeeklyReport:
    today = _today(today)
    pm = _profile(user)
    own = list(pm_projects_now(pm))
    if not own:
        raise PermissionDenied('Сводный отчёт сдаёт действующий ПМ.')
    text = (text or '').strip()
    if not text:
        raise ReportError('Напишите отчёт.')
    week = _week(week_start, today)
    chosen = [p for p in own if p.pk in {int(x) for x in project_ids if str(x).isdigit()}]
    on_time = week.start <= today <= week.end and today.weekday() in ON_TIME_WEEKDAYS
    try:
        with transaction.atomic():
            report = PmWeeklyReport.objects.create(pm=pm, author=user, week_start=week.start, text=text[:20000],
                                                   submitted_on=today, on_time=on_time)
            report.projects.set(chosen)
    except IntegrityError:
        raise ReportError('Отчёт за эту неделю уже сдан.') from None
    return report


# =============================================================== оценки

def evaluable(lead, project):
    """Стажёры, которых тимлид может оценить в проекте: активные участники
    своего направления (не тимлиды и не штатные сотрудники)."""
    from apps.teams.selectors import staff_intern_ids

    directions = lead_directions(lead, project)
    if not directions or project.status not in WRITABLE:
        return TeamMember.objects.none()
    return (TeamMember.objects.filter(project=project, role__in=directions, status=TeamMember.Status.ACTIVE,
                                      intern__isnull=False, intern__is_archived=False)
            .exclude(intern_id__in=staff_intern_ids()).select_related('intern').order_by('intern__full_name'))


def evaluate(user, intern, project, score, comment='', *, week_start=None, today=None) -> tuple[WeeklyEvaluation, bool]:
    """Поставить (или поправить свою) оценку за неделю. (оценка, создана ли)."""
    today = _today(today)
    lead = _profile(user)
    try:
        score = int(score)
    except (TypeError, ValueError):
        raise ReportError('Оценка — число от 1 до 10.') from None
    if not WeeklyEvaluation.MIN <= score <= WeeklyEvaluation.MAX:
        raise ReportError('Оценка — число от 1 до 10.')
    if not evaluable(lead, project).filter(intern=intern).exists():
        raise PermissionDenied('Оценивает активный тимлид проекта — только стажёров своего направления.')
    week = _week(week_start, today)
    comment = (comment or '').strip()[:4000]
    with transaction.atomic():
        existing = WeeklyEvaluation.objects.select_for_update().filter(
            intern=intern, project=project, week_start=week.start).first()
        if existing:
            if existing.lead_id != lead.pk:
                raise PermissionDenied('Оценку за эту неделю уже поставил другой тимлид — её нельзя изменить.')
            existing.score, existing.comment, existing.author = score, comment, user
            existing.save(update_fields=['score', 'comment', 'author', 'updated_at'])
            return existing, False
        try:
            with transaction.atomic():
                role = evaluable(lead, project).filter(intern=intern).values_list('role', flat=True).first()
                made = WeeklyEvaluation.objects.create(
                    intern=intern, project=project, direction=role or lead_direction(lead), week_start=week.start,
                    lead=lead, author=user, score=score, comment=comment)
        except IntegrityError:
            raise ReportError('Оценку за эту неделю уже поставили — обновите страницу.') from None
    return made, True


# ========================================================== напоминания

def remind(today=None) -> int:
    """Внутренние уведомления в порталы (не Telegram): в пятницу и субботу —
    тем, у кого обязательный отчёт ещё ожидается. Без дублей (dedup_key)."""
    from apps.dashboard.metrics import pm_report_obligations
    from apps.interns.models import Intern
    from apps.notifications.models import NotificationLevel
    from apps.notifications.services import notify

    today = _today(today)
    if today.weekday() not in ON_TIME_WEEKDAYS:
        return 0
    week = week_of(today, today)
    created = 0
    leads = {}
    for item in lead_obligations(week, today):
        if item.status == 'pending':
            leads.setdefault(item.lead_id, []).append(item.project.name)
    from apps.tgbot import notify as telegram

    for intern in Intern.objects.filter(pk__in=leads):
        title = f'Отчёт за неделю: {", ".join(leads[intern.pk])}'
        created += bool(notify(title, level=NotificationLevel.INFO,
                               description='Сдайте еженедельный отчёт по каждому закреплённому проекту.',
                               intern=intern, dedup_key=f'lead-report:{week.key}'))
        telegram.report_reminder(intern, f'{title}. Сдать — в Control: «Отчёты и оценки».', week.key)
    done = set(PmWeeklyReport.objects.filter(week_start=week.start).values_list('pm_id', flat=True))
    for item in pm_report_obligations(week):
        if item['intern_id'] in done:
            continue
        intern = Intern.objects.filter(pk=item['intern_id']).first()
        if intern:
            created += bool(notify('Сводный отчёт ПМ за неделю', level=NotificationLevel.INFO,
                                   description='Срок — пятница или суббота.', intern=intern,
                                   dedup_key=f'pm-report:{week.key}'))
            telegram.report_reminder(intern, 'Сводный отчёт ПМ за неделю: срок — пятница или суббота. '
                                             'Сдать — в Control: «Отчёты».', week.key)
    return created

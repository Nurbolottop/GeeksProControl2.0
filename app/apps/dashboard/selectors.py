"""Блок «Требует внимания» и очереди решений руководителя (UI-02).

Это список конкретных управленческих событий, а не статус проекта:
каждый пункт ведёт на объект или действие. Статус «Проблемный» здесь
не используется.
"""
import datetime

from django.urls import reverse
from django.utils import timezone

from apps.dashboard import metrics
from apps.projects import selectors as project_selectors
from apps.projects.models import ProjectStageKey

LEVEL_ORDER = {'danger': 0, 'warning': 1, 'info': 2}
DELIVERY_STAGES = (ProjectStageKey.DELIVERY, ProjectStageKey.PRODUCTION)
MEETINGS_LOOKBACK_DAYS = 14
GRADUATE_DECISION_DAYS = 14


def _item(level, category, text, url, action='Открыть'):
    return {'level': level, 'category': category, 'text': text, 'url': url, 'action': action}


def pending_decisions() -> list[dict]:
    """Очереди, где ждут решения руководителя (с количеством)."""
    from apps.documents.models import ProjectBrief
    from apps.interns.models import GraduateStatus, Intern, ResumeBankStatus

    queues = [
        ('Заявки с брифа', ProjectBrief.objects.filter(project__isnull=True).count(),
         reverse('documents:brief_requests')),
        ('Банк резюме — на проверке', Intern.objects.active().filter(
            resume_bank_status=ResumeBankStatus.PENDING).count(), reverse('interns:resume_bank')),
        ('Выпускники — на проверке', Intern.objects.active().filter(
            graduate_status=GraduateStatus.PENDING).count(), reverse('interns:graduates')),
        ('Ждут проект', Intern.objects.active().filter(
            graduate_status=GraduateStatus.WAITING).count(), reverse('interns:waiting')),
    ]
    return [{'label': label, 'count': count, 'url': url, 'title': f'{label} — {count}'}
            for label, count, url in queues]


def _project_items(items):
    from apps.documents.models import Document, DocumentStatus, DocumentType

    today = timezone.localdate()
    active = list(project_selectors.active_projects())  # prefetch team_members__intern

    for project in active:
        if not project.has_pm:
            items.append(_item('warning', 'Команда', f'{project.name} — не назначен ПМ',
                               project.get_absolute_url() + '?tab=team', 'Назначить'))
        if not project.leads:
            items.append(_item('warning', 'Команда', f'{project.name} — не назначен тимлид',
                               project.get_absolute_url() + '?tab=team', 'Назначить'))
        if project.planned_end_date and project.planned_end_date < today:
            days = (today - project.planned_end_date).days
            items.append(_item('danger', 'Сроки', f'{project.name} — дедлайн прошёл {days} дн. назад',
                               project.get_absolute_url()))

    unset = [p for p in active if not p.primary_stage_id]
    if unset:
        items.append(_item('info', 'Этапы', f'Основной этап не выбран: {len(unset)} пр.',
                           reverse('projects:list') + '?primary_stage=unset', 'Выбрать'))

    # Обязательные документы — у проектов на передаче заказчику (одним запросом)
    # Этап — выбранный основной, иначе подсказка системы
    delivery = [p for p in active if (p.primary_stage.key if p.primary_stage_id else p.current_stage) in DELIVERY_STAGES]
    if delivery:
        required = list(DocumentType.objects.filter(required_for_delivery=True))
        present = set(
            Document.objects.active().filter(project__in=delivery, doc_type__in=required)
            .exclude(status__in=[DocumentStatus.CANCELLED, DocumentStatus.EXPIRED])
            .values_list('project_id', 'doc_type_id'),
        )
        for project in delivery:
            missing = [t.name for t in required if (project.pk, t.pk) not in present]
            if missing:
                items.append(_item('danger', 'Документы',
                                   f'{project.name} — на передаче, нет: {", ".join(missing)}',
                                   project.get_absolute_url() + '?tab=documents', 'К документам'))


def _meeting_items(items):
    from apps.attendance.models import GroupMeeting, MeetingKind

    today = timezone.localdate()
    since = today - datetime.timedelta(days=MEETINGS_LOOKBACK_DAYS)
    missed = (
        GroupMeeting.objects.filter(date__gte=since, date__lt=today)
        .exclude(kind=MeetingKind.OTHER)
        .filter(status__in=[GroupMeeting.Status.MISSED, GroupMeeting.Status.PLANNED])
        .select_related('group__project').order_by('-date')
    )
    for meeting in missed:
        project = meeting.group.project if meeting.group_id else None
        where = f' ({project.name})' if project else ''
        if meeting.status == GroupMeeting.Status.MISSED:
            text = f'Собрание {meeting.date:%d.%m}{where} не проведено'
        else:
            text = f'Собрание {meeting.date:%d.%m}{where} прошло, но не отмечено'
        items.append(_item('warning', 'Собрания', text,
                           reverse('attendance:meeting_detail', args=[meeting.pk])))


def _report_items(items):
    week = metrics.last_complete_week()
    reports = metrics.pm_reports(week, timezone.localdate())
    for person in reports['missing_people']:
        items.append(_item('warning', 'Отчёты',
                           f'{person["name"]} — нет отчёта ПМ за {week.start:%d.%m}–{week.end:%d.%m}',
                           reverse('interns:detail', args=[person['intern_id']])))


def _decision_items(items):
    from apps.interns.models import GraduateStatus
    from apps.interns.services import graduated_interns

    today = timezone.localdate()
    for queue in pending_decisions():
        if queue['count']:
            items.append(_item('info', 'Решения', f'{queue["label"]}: {queue["count"]}', queue['url'], 'Разобрать'))
    # Выпускник, зависший «на проверке» дольше двух недель — отдельно по имени
    for intern in graduated_interns():
        if intern.graduate_status != GraduateStatus.PENDING or not intern.graduated_at:
            continue
        days = (today - intern.graduated_at).days
        if days > GRADUATE_DECISION_DAYS:
            items.append(_item('warning', 'Решения',
                               f'{intern.full_name} — на проверке выпуска уже {days} дн., нужно решить',
                               intern.get_absolute_url(), 'Решить'))


def attention_items() -> list[dict]:
    """Все события «Требует внимания», важные — первыми."""
    items: list[dict] = []
    _project_items(items)
    _meeting_items(items)
    _report_items(items)
    _decision_items(items)
    items.sort(key=lambda item: LEVEL_ORDER.get(item['level'], 3))
    return items

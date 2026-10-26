"""Бизнес-логика проектов (service layer, ТЗ §46)."""
import datetime

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.projects.models import (
    DeadlineStatus,
    Project,
    ProjectStage,
    ProjectStageKey,
    ProjectStatus,
    ProjectStatusHistory,
    lifecycle_stages,
)

# Пороговые значения автоматических проверок (ТЗ §22).
# Выносятся в settings, здесь — значения по умолчанию.
RISK_DAYS = getattr(settings, 'DEADLINE_RISK_DAYS', 7)
RISK_PROGRESS = getattr(settings, 'DEADLINE_RISK_PROGRESS', 70)
HIGH_RISK_DAYS = getattr(settings, 'DEADLINE_HIGH_RISK_DAYS', 3)
HIGH_RISK_PROGRESS = getattr(settings, 'DEADLINE_HIGH_RISK_PROGRESS', 90)
INACTIVE_DAYS = getattr(settings, 'PROJECT_INACTIVE_DAYS', 3)
# Дедлайн по умолчанию — если не задан явно, считаем от даты начала проекта.
DEFAULT_DEADLINE_DAYS = getattr(settings, 'PROJECT_DEFAULT_DEADLINE_DAYS', 60)

# Изменения этих полей проекта фиксируются в истории (ТЗ §27).
TRACKED_FIELDS = {
    'status': 'Статус',
    'current_stage': 'Этап',
    'planned_end_date': 'Плановая дата завершения',
    'progress': 'Прогресс',
    'priority': 'Приоритет',
    'project_type': 'Тип проекта',
}

# Статусы, после которых команда больше не работает над проектом:
# участников надо освобождать, иначе они «зависают» занятыми навсегда.
TERMINAL_STATUSES = {
    ProjectStatus.COMPLETED, ProjectStatus.CANCELLED, ProjectStatus.REFUSED,
}


def release_team(project: Project, when: datetime.date | None = None) -> list[int]:
    """Освобождает команду ЭТОГО проекта (ТЗ §42.3, UI-03B).

    Трогает только активные участия этого проекта — другие проекты людей
    не меняются. Повторный вызов ничего не делает (идемпотентно).

    При успешном закрытии (статус «Завершён»):
    * активные новые стажёры становятся выпускниками (основание выпуска
      считает apps.dashboard.graduation по закрытому участию); свободным —
      отметка «на проверке», «Готов к распределению» и приглашение в бота
      после commit;
    * уже выпускники статус выпускника сохраняют — у них завершается только
      это участие;
    * замороженные (на дату закрытия) не выпускаются: участие закрывается
      с причиной «Стажировка заморожена»;
    * тимлиды — сотрудники, не стажёры: только уведомление в портал.

    Возвращает id новых выпускников.
    """
    from django.db import transaction

    with transaction.atomic():
        return _release_team(project, when)


def _release_team(project, when):
    from django.db import transaction

    from apps.dashboard.graduation import compute
    from apps.interns.models import GraduateStatus, Intern, InternStatus
    from apps.teams.models import TeamMember, TeamRole
    from apps.teams.selectors import lead_intern_ids
    from apps.teams.services import still_on_a_live_project

    when = when or timezone.localdate()
    # Блокируем строки участий: одобрение заморозки (apps.interns.freeze)
    # берёт те же строки — кто второй, тот видит уже закрытое участие
    locked = list(
        project.team_members.select_for_update(of=('self',))
        .filter(status=TeamMember.Status.ACTIVE).order_by('pk').values_list('pk', flat=True),
    )
    members = list(TeamMember.objects.filter(pk__in=locked).select_related('intern'))
    if not members:
        return []
    leads = set(lead_intern_ids())
    completed = project.status == ProjectStatus.COMPLETED
    people = {m.intern_id for m in members if m.intern_id}
    graduates_before = compute(people, exclude_project_id=project.pk).ids if completed else set()

    frozen_ids, ended_ids, lead_ids, interns = [], [], set(), []
    for member in members:
        intern = member.intern
        is_lead = member.role == TeamRole.TEAM_LEAD or (intern is not None and intern.pk in leads)
        if intern is not None and not is_lead and intern.status == InternStatus.PAUSED:
            frozen_ids.append(member.pk)
            continue
        ended_ids.append(member.pk)
        if intern is None:
            continue
        if is_lead:
            if member.role == TeamRole.TEAM_LEAD:
                lead_ids.add(intern.pk)
            continue
        interns.append(intern.pk)
    TeamMember.objects.filter(pk__in=frozen_ids).update(
        status=TeamMember.Status.LEFT, left_at=when, left_reason=TeamMember.LeftReason.FROZEN,
    )
    TeamMember.objects.filter(pk__in=ended_ids).update(
        status=TeamMember.Status.LEFT, left_at=when, left_reason=TeamMember.LeftReason.PROJECT_ENDED,
    )
    from apps.tasks.workflow import release_stale

    release_stale(project_ids=[project.pk], reason='команда проекта освобождена')

    new_graduates = []
    if completed:
        from apps.interns.models import Internship

        # Выпуск — событие периода стажировки: первый выпуск в периоде
        # записывается один раз, следующие закрытые проекты его не дублируют
        Internship.objects.filter(intern_id__in=interns, ended_on__isnull=True, end_reason='',
                                  graduated_on__isnull=True).update(graduated_on=when)
        new_graduates = [pk for pk in interns if pk not in graduates_before]
        free = [pk for pk in interns if not still_on_a_live_project(pk)]
        free_new = [pk for pk in new_graduates if pk in free]
        Intern.objects.filter(pk__in=free_new).update(graduate_status=GraduateStatus.PENDING)
        # Освободился — «Готов к распределению» (замороженных не трогаем)
        Intern.objects.filter(pk__in=free, status=InternStatus.ACTIVE).update(status=InternStatus.READY)
        if free_new:
            # Telegram — только после commit: откат закрытия не должен
            # оставить людям поздравление с несостоявшимся выпуском.
            from apps.graduate_bot.services import invite_graduates

            transaction.on_commit(
                lambda: invite_graduates(project, Intern.objects.filter(pk__in=free_new)),
            )
    notify_leads_project_closed(project, Intern.objects.filter(pk__in=lead_ids))
    return new_graduates


def notify_leads_project_closed(project: Project, leads) -> None:
    """«Проект завершён / закрыт» — каждому тимлиду в его портал."""
    from apps.notifications.models import NotificationLevel
    from apps.notifications.services import notify

    if project.status == ProjectStatus.COMPLETED:
        title = f'Проект «{project.name}» завершён'
        description = (
            'Проект сдан, команда освобождена. Спасибо за работу! '
            'Проект остался в вашей истории, а из портала он пропал.'
        )
        level = NotificationLevel.SUCCESS
    else:
        title = f'Проект «{project.name}» закрыт: {project.get_status_display().lower()}'
        description = (
            'Работа по проекту остановлена, команда освобождена — '
            'проект больше не показывается в вашем портале.'
        )
        level = NotificationLevel.WARNING
    for person in leads:
        notify(
            title, level=level, description=description, intern=person,
            dedup_key=f'project-closed:{project.pk}:{project.status}',
        )


def calculate_deadline_status(
    project: Project, today: datetime.date | None = None,
) -> str:
    """Статус срока проекта (ТЗ §8): rule-based, без AI.

    По графику / Риск задержки / Отставание / Просрочен.
    """
    if project.status != ProjectStatus.ACTIVE:
        # Завершён / приостановлен / отменён / отказ — контроль срока не ведётся
        return ''
    if not project.planned_end_date:
        return DeadlineStatus.ON_TRACK

    today = today or timezone.localdate()
    days_left = (project.planned_end_date - today).days

    if days_left < 0:
        return DeadlineStatus.OVERDUE
    if days_left <= HIGH_RISK_DAYS and project.progress < HIGH_RISK_PROGRESS:
        return DeadlineStatus.BEHIND
    if days_left <= RISK_DAYS and project.progress < RISK_PROGRESS:
        return DeadlineStatus.AT_RISK
    return DeadlineStatus.ON_TRACK


@transaction.atomic
def ensure_group(project: Project):
    """Команда проекта создаётся вместе с проектом.

    Отдельной сущности «создать команду» нет: у каждого проекта своя
    группа в текущем потоке, туда и добавляются ПМ, тимлиды и стажёры.
    """
    from apps.flows.models import Flow, Group

    if getattr(project, 'group', None) is not None:
        return project.group

    flow = project.flow
    if flow is None:
        flow = (
            Flow.objects.filter(status=Flow.Status.ACTIVE)
            .order_by('-number').first()
            or Flow.objects.order_by('-number').first()
        )
    if flow is None:
        flow = Flow.objects.create(number=1, status=Flow.Status.ACTIVE)

    number = (flow.groups.aggregate(m=Max('number'))['m'] or 0) + 1
    group = Group.objects.create(flow=flow, number=number, project=project)

    fields = []
    if project.flow_id != flow.pk:
        project.flow = flow
        fields.append('flow')
    if not project.number_in_flow:
        project.number_in_flow = number
        fields.append('number_in_flow')
    if fields:
        project.save(update_fields=[*fields, 'updated_at'])
    return group


# Этап разработки зависит от типа проекта: у веба Frontend, у мобильного
# «Мобильная разработка». Это одна и та же работа, поэтому при смене типа
# этап переименовываем, а не заводим заново — статус, даты и прогресс
# остаются на месте.
DEV_STAGE_PAIR = (ProjectStageKey.FRONTEND, ProjectStageKey.MOBILE_DEV)


def sync_stages_to_type(project: Project) -> None:
    """Приводит набор этапов к типу проекта.

    Тип указывает ПМ уже после создания, а от него зависит, какой этап
    разработки нужен. Уже начатый этап разработки переносим на новый ключ
    вместе со статусом и датами; недостающие этапы добавляем, лишние
    убираем только если их ещё не трогали, и заново расставляем порядок.
    """
    desired = lifecycle_stages(project.project_type)
    stages = {stage.key: stage for stage in project.stages.all()}

    wanted_dev = next((key for key in DEV_STAGE_PAIR if key in desired), None)
    stale_dev = next(
        (key for key in DEV_STAGE_PAIR if key != wanted_dev and key in stages), None,
    )
    if wanted_dev and stale_dev and wanted_dev not in stages:
        stage = stages.pop(stale_dev)
        stage.key = wanted_dev
        stage.save(update_fields=['key', 'updated_at'])
        stages[wanted_dev] = stage
        if project.current_stage == stale_dev:
            project.current_stage = wanted_dev
            project.save(update_fields=['current_stage', 'updated_at'])

    for index, key in enumerate(desired):
        stage = stages.get(key)
        if stage is None:
            ProjectStage.objects.create(project=project, key=key, order=index)
        elif stage.order != index:
            stage.order = index
            stage.save(update_fields=['order', 'updated_at'])
    for key, stage in stages.items():
        untouched = (
            stage.status == ProjectStage.Status.NOT_STARTED
            and not stage.progress and not stage.start_date and not stage.end_date
        )
        if key not in desired and untouched:
            stage.delete()


@transaction.atomic
def create_project(project: Project, user=None) -> Project:
    """Сохраняет новый проект и создаёт полный набор этапов жизненного цикла.

    Дата начала и дедлайн подставляются сами, если их не задали руками:
    начало — сегодня, дедлайн — начало + DEFAULT_DEADLINE_DAYS дней.
    Дальше это обычные редактируемые поля, а не что-то пересчитываемое.
    """
    if not project.start_date:
        project.start_date = timezone.localdate()
    if not project.planned_end_date:
        project.planned_end_date = (
            project.start_date + datetime.timedelta(days=DEFAULT_DEADLINE_DAYS)
        )
    project.last_activity_at = timezone.now()
    project.save()
    stages = [
        ProjectStage(project=project, key=key, order=index)
        for index, key in enumerate(lifecycle_stages(project.project_type))
    ]
    ProjectStage.objects.bulk_create(stages)
    ProjectStatusHistory.objects.create(
        project=project, field='created',
        new_value='Проект создан', user=user,
    )
    ensure_group(project)
    # Автоматические чек-листы задач отключены: задачи заводятся руками
    return project


def _display(project: Project, field: str, value) -> str:
    if value is None or value == '':
        return ''
    if field in ('status', 'current_stage', 'priority'):
        # value — «сырое» значение choices; показываем человекочитаемую метку
        choices = dict(Project._meta.get_field(field).choices)
        return str(choices.get(value, value))
    return str(value)


@transaction.atomic
def update_project(
    project: Project, old_values: dict, user=None, reason: str = '',
) -> Project:
    """Сохраняет проект и пишет в историю изменения отслеживаемых полей.

    «Завершён» здесь не ставится — только apps.projects.closure.close_project.
    """
    from django.core.exceptions import ValidationError

    old_status = old_values.get('status')
    if project.status == ProjectStatus.COMPLETED and old_status != ProjectStatus.COMPLETED:
        raise ValidationError('Проект закрывается только процедурой закрытия руководителем.')
    if old_status == ProjectStatus.COMPLETED and project.status != ProjectStatus.COMPLETED:
        raise ValidationError('Закрытый проект не открывается повторно.')
    records = []
    for field, label in TRACKED_FIELDS.items():
        old = old_values.get(field)
        new = getattr(project, field)
        if old != new:
            records.append(ProjectStatusHistory(
                project=project, field=label,
                old_value=_display(project, field, old),
                new_value=_display(project, field, new),
                reason=reason, user=user,
            ))
    project.last_activity_at = timezone.now()
    project.save()
    if old_values.get('project_type') != project.project_type:
        # сменили тип — этап разработки должен стать Frontend или Mobile
        sync_stages_to_type(project)
    if project.status in TERMINAL_STATUSES and old_status != project.status:
        release_team(project)
    if records:
        ProjectStatusHistory.objects.bulk_create(records)
        from apps.audit.services import log as audit_log
        for record in records:
            audit_log(
                project, f'Изменение: {record.field}',
                old_value=record.old_value, new_value=record.new_value,
                reason=reason, user=user,
            )
    return project


@transaction.atomic
def move_project_to_stage(project: Project, stage_key: str, user=None) -> Project:
    """Перемещение проекта между этапами (Kanban, ТЗ §6.4) с записью в историю."""
    old_stage = project.current_stage
    if old_stage == stage_key:
        return project
    project.current_stage = stage_key
    project.last_activity_at = timezone.now()
    project.save(update_fields=['current_stage', 'last_activity_at', 'updated_at'])
    ProjectStatusHistory.objects.create(
        project=project, field='Этап',
        old_value=dict(ProjectStageKey.choices).get(old_stage, old_stage),
        new_value=dict(ProjectStageKey.choices).get(stage_key, stage_key),
        user=user,
    )
    return project


def _resync_current_stage(project: Project, user=None) -> None:
    """project.current_stage — это первый незавершённый этап по порядку.

    Пересчитывается при каждом сохранении любого этапа, поэтому неважно,
    каким путём его поменяли — кнопкой «Завершить» или обычным «Изменить» —
    бейдж «Этап» никогда не разойдётся с тем, что реально сделано: не
    отстанет (переоткрыли пройденный этап) и не убежит вперёд (этап
    завершили через форму, а не через «Завершить»).
    """
    next_stage = (
        project.stages.exclude(status=ProjectStage.Status.DONE)
        .order_by('order').first()
    )
    if next_stage:
        target_key = next_stage.key
    else:
        last_stage = project.stages.order_by('-order').first()
        target_key = last_stage.key if last_stage else None
    if target_key and target_key != project.current_stage:
        move_project_to_stage(project, target_key, user=user)


def _resync_progress(project: Project) -> None:
    """project.progress — процент завершённых этапов.

    Служебный последний этап «Завершён» не считается: иначе прогресс
    никогда не дойдёт до 100% раньше самого завершения проекта, а это
    одно из обязательных условий кнопки «Завершить проект».
    """
    stages = list(project.stages.exclude(key=ProjectStageKey.COMPLETED))
    if not stages:
        return
    done = sum(1 for s in stages if s.status == ProjectStage.Status.DONE)
    percent = round(100 * done / len(stages))
    if percent != project.progress:
        project.progress = percent
        project.save(update_fields=['progress', 'updated_at'])


@transaction.atomic
def update_stage(stage: ProjectStage, user=None) -> ProjectStage:
    """Сохранение этапа с автоматикой дат начала и завершения."""
    if stage.status == ProjectStage.Status.IN_PROGRESS and not stage.start_date:
        stage.start_date = timezone.localdate()
    if stage.status == ProjectStage.Status.DONE and not stage.end_date:
        stage.end_date = timezone.localdate()
    if stage.status == ProjectStage.Status.NOT_STARTED:
        stage.start_date = None
        stage.end_date = None
    stage.save()
    _touch_project(stage.project)
    _resync_current_stage(stage.project, user=user)
    _resync_progress(stage.project)
    return stage


@transaction.atomic
def complete_stage(stage: ProjectStage, user=None) -> ProjectStage:
    """Завершение этапа: ставит дату завершения и двигает проект дальше."""
    stage.status = ProjectStage.Status.DONE
    stage.end_date = timezone.localdate()
    if not stage.start_date:
        stage.start_date = timezone.localdate()
    stage.save()

    project = stage.project
    ProjectStatusHistory.objects.create(
        project=project, field=f'Этап «{stage.get_key_display()}»',
        new_value='Завершён', user=user,
    )
    _touch_project(project)
    _resync_current_stage(project, user=user)
    _resync_progress(project)
    return stage


@transaction.atomic
def extend_stage_deadline(
    stage: ProjectStage, new_deadline, reason: str, user=None,
) -> ProjectStage:
    """Продление дедлайна этапа. Причина обязательна и пишется в историю."""
    old_deadline = stage.deadline
    stage.deadline = new_deadline
    stage.save(update_fields=['deadline', 'updated_at'])

    ProjectStatusHistory.objects.create(
        project=stage.project,
        field=f'Deadline этапа «{stage.get_key_display()}»',
        old_value=f'{old_deadline:%d.%m.%Y}' if old_deadline else '',
        new_value=f'{new_deadline:%d.%m.%Y}',
        reason=reason, user=user,
    )
    from apps.audit.services import log as audit_log
    audit_log(
        stage.project, f'Продление этапа «{stage.get_key_display()}»',
        old_value=f'{old_deadline:%d.%m.%Y}' if old_deadline else '',
        new_value=f'{new_deadline:%d.%m.%Y}', reason=reason, user=user,
    )
    _touch_project(stage.project)
    return stage


def _touch_project(project: Project) -> None:
    project.last_activity_at = timezone.now()
    project.save(update_fields=['last_activity_at', 'updated_at'])


# --- Этап проекта (2.0, 11F) -------------------------------------------
# У проекта один этап — его вручную выбирают руководитель или ПМ проекта.
# Поле осталось primary_stage (история миграций), но «основного» и
# «параллельных» этапов больше нет: при выборе этапы до него отмечаются
# завершёнными, выбранный — в работе, после него — не начаты. Так готовность
# (project.progress) и current_stage для старых экранов совпадают с выбором.
# «Продакшен» — последний рабочий этап: выбран — значит, всё сделано (100%).

def can_set_primary_stage(user, project: Project) -> bool:
    """Руководитель / администратор или ПМ, назначенный на этот проект."""
    from apps.accounts.access import has_full_access
    from apps.accounts.models import User
    from apps.teams.models import TeamMember, TeamRole

    if has_full_access(user):
        return True
    if not user.is_authenticated or user.role != User.Role.PROJECT_MANAGER:
        return False
    return TeamMember.objects.filter(
        project=project, intern__user=user, role=TeamRole.PROJECT_MANAGER,
        status=TeamMember.Status.ACTIVE,
    ).exists()


def _sync_stages_to(project: Project, chosen: ProjectStage) -> None:
    """Статусы этапов по выбранному: до него — завершены, он — в работе."""
    today = timezone.localdate()
    working = list(project.stages.exclude(key=ProjectStageKey.COMPLETED).order_by('order'))
    final = chosen.key == ProjectStageKey.PRODUCTION
    for stage in working:
        if stage.order < chosen.order or (final and stage.pk == chosen.pk):
            status = ProjectStage.Status.DONE
        elif stage.pk == chosen.pk:
            status = ProjectStage.Status.IN_PROGRESS
        else:
            status = ProjectStage.Status.NOT_STARTED
        if stage.status == status:
            continue
        stage.status = status
        if status == ProjectStage.Status.NOT_STARTED:
            stage.start_date = stage.end_date = None
        else:
            stage.start_date = stage.start_date or today
            stage.end_date = (stage.end_date or today) if status == ProjectStage.Status.DONE else None
        stage.save()
    project.current_stage = chosen.key
    project.save(update_fields=['current_stage', 'updated_at'])
    _resync_progress(project)


@transaction.atomic
def set_primary_stage(project: Project, stage: ProjectStage, user) -> Project:
    """Выбрать этап проекта. Проверяет права и что этап — этого проекта."""
    from django.core.exceptions import PermissionDenied, ValidationError

    if stage.project_id != project.pk:
        raise ValidationError('Этап принадлежит другому проекту.')
    if not can_set_primary_stage(user, project):
        raise PermissionDenied('Этап меняют руководитель или ПМ этого проекта.')
    old = project.primary_stage
    project.primary_stage = stage
    project.primary_stage_set_by = user
    project.primary_stage_set_at = timezone.now()
    project.stages_checked_at = project.primary_stage_set_at
    project.last_activity_at = project.primary_stage_set_at
    project.save(update_fields=['primary_stage', 'primary_stage_set_by', 'primary_stage_set_at',
                                'stages_checked_at', 'last_activity_at', 'updated_at'])
    _sync_stages_to(project, stage)
    if old != stage:
        ProjectStatusHistory.objects.create(
            project=project, field='Этап',
            old_value=old.get_key_display() if old else '',
            new_value=stage.get_key_display(), user=user,
        )
    return project


# --- Набор стажёров (2.0) --------------------------------------------------

def _recruitment_text(is_open: bool, roles, kind='all') -> str:
    if not is_open:
        return 'закрыт'
    from apps.teams.models import TeamRole

    labels = dict(TeamRole.choices)
    text = f'открыт: {", ".join(labels.get(r, r) for r in roles)}' if roles else 'открыт (все направления)'
    who = {'new': 'только новые', 'old': 'только старые'}.get(kind)
    return f'{text} · {who}' if who else text


@transaction.atomic
def set_recruitment(project: Project, is_open: bool, user, roles=None, kind='all') -> Project:
    """Открыть / закрыть набор. Только руководитель (полный доступ).

    roles (11F) — направления, на которые открыт набор; пусто — на все.
    kind — кого берём: all / new (только новых) / old (только старых).
    При открытом наборе повторный вызов меняет только направления и kind.
    """
    from django.core.exceptions import PermissionDenied, ValidationError

    from apps.accounts.access import has_full_access
    from apps.teams.models import TeamRole
    from apps.teams.rules import DIRECTION_ROLES

    if not has_full_access(user):
        raise PermissionDenied('Набор открывает и закрывает руководитель.')
    allowed = [r for r in DIRECTION_ROLES if r != TeamRole.OTHER]
    unknown = [r for r in roles or [] if r not in allowed]
    if unknown:
        raise ValidationError(f'Неизвестное направление: {", ".join(unknown)}.')
    if kind not in ('all', 'new', 'old'):
        raise ValidationError('Кого берём: всех, только новых или только старых.')
    # порядок — как в списке направлений, без повторов
    roles = [r for r in allowed if r in set(roles or [])] if is_open else []
    kind = kind if is_open else 'all'
    with transaction.atomic():
        # Та же блокировка, что у вступления (teams.rules.lock_and_recheck)
        locked = Project.objects.select_for_update().get(pk=project.pk)
        if (locked.recruitment_open == is_open and list(locked.recruitment_roles or []) == roles
                and locked.recruitment_kind == kind):
            project.recruitment_open, project.recruitment_roles, project.recruitment_kind = is_open, roles, kind
            return project
        if is_open and locked.status != ProjectStatus.ACTIVE:
            raise ValidationError('Набор открывается только в идущем проекте.')
        old = _recruitment_text(locked.recruitment_open, locked.recruitment_roles or [], locked.recruitment_kind)
        _write_recruitment(project, is_open, user, roles, old, kind)
    return project


def _write_recruitment(project, is_open, user, roles=(), old=None, kind='all'):
    project.recruitment_open = is_open
    project.recruitment_roles = list(roles)
    project.recruitment_kind = kind
    project.recruitment_changed_by = user
    project.recruitment_changed_at = timezone.now()
    project.save(update_fields=['recruitment_open', 'recruitment_roles', 'recruitment_kind', 'recruitment_changed_by',
                                'recruitment_changed_at', 'updated_at'])
    ProjectStatusHistory.objects.create(
        project=project, field='Набор',
        old_value=old or ('закрыт' if is_open else 'открыт'), new_value=_recruitment_text(is_open, roles, kind), user=user,
    )
    return project


def projects_open_for_recruitment():
    """Проекты, которые бот может показывать для вступления: идущие и с открытым набором."""
    return Project.objects.active().filter(status=ProjectStatus.ACTIVE, recruitment_open=True)

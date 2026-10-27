"""Бизнес-логика стажёров: пересчёт рейтинга (ТЗ §12.1)."""
from decimal import Decimal

from apps.interns.models import (
    GraduateStatus, Intern, InternEvaluation, InternStatus, Position,
    ProfileFormLink, ResumeBankStatus,
)


def add_evaluation(evaluation: InternEvaluation) -> InternEvaluation:
    """Сохраняет оценку и пересчитывает средний рейтинг стажёра."""
    evaluation.save()
    recalculate_rating(evaluation.intern)
    return evaluation


def recalculate_rating(intern: Intern) -> None:
    evaluations = list(intern.evaluations.all())
    if evaluations:
        total = sum(Decimal(str(e.average)) for e in evaluations)
        intern.rating = round(total / len(evaluations), 2)
    else:
        intern.rating = None
    intern.save(update_fields=['rating', 'updated_at'])


def graduated_interns() -> list[Intern]:
    """Стажёры со статусом выпускника («Выпускники»): на проверке или отказались.

    Источник истины — ``Intern.graduate_status``, а не сам факт наличия
    завершённого проекта в истории: как только ПМ назначает стажёра на
    новый проект (:func:`apps.interns.views.intern_project_add`), статус
    сбрасывается и человек пропадает из этого списка — даже если у него
    остаётся членство в завершённом проекте в прошлом.
    """
    from apps.projects.models import ProjectStatus
    from apps.teams.models import TeamMember
    from apps.teams.selectors import lead_intern_ids

    interns = list(
        Intern.objects.filter(graduate_status__in=GraduateStatus.values)
        .exclude(pk__in=lead_intern_ids())
        .select_related('specialization'),
    )
    if not interns:
        return interns

    memberships = (
        TeamMember.objects.filter(
            intern_id__in=[i.pk for i in interns],
            status=TeamMember.Status.LEFT,
            project__status=ProjectStatus.COMPLETED,
        )
        .select_related('project')
        .order_by('intern_id', '-project__actual_end_date', '-left_at')
    )
    latest_by_intern = {}
    for member in memberships:
        latest_by_intern.setdefault(member.intern_id, member)

    for intern in interns:
        member = latest_by_intern.get(intern.pk)
        intern.graduated_project = member.project if member else None
        intern.graduated_at = (
            (member.project.actual_end_date or member.left_at) if member else None
        )
    interns.sort(key=lambda i: i.graduated_at or i.created_at.date(), reverse=True)
    return interns


def issue_profile_form_link(
    user=None, ttl_days: int | None = None, project=None,
) -> ProfileFormLink:
    """Выпускает новую ссылку на анкету, гася прежние того же вида.

    Активной может быть одна ссылка на проект (и одна общая, без
    проекта): как только выпустили новую, старая перестаёт открываться —
    в этом и смысл «непостоянной» ссылки. Ссылки других проектов не
    трогаем. `ttl_days` — через сколько дней ссылка закроется сама;
    None — бессрочно, до замены или ручного отключения.
    """
    from datetime import timedelta

    from django.utils import timezone

    now = timezone.now()
    ProfileFormLink.objects.filter(is_active=True, project=project).update(
        is_active=False, deactivated_at=now,
    )
    return ProfileFormLink.objects.create(
        created_by=user if user and user.is_authenticated else None,
        expires_at=now + timedelta(days=ttl_days) if ttl_days else None,
        project=project,
    )


def active_profile_form_link(project=None) -> ProfileFormLink | None:
    """Действующая ссылка на анкету (общая или проекта) или None.

    Истёкшую по сроку гасим на месте, чтобы список и анкета одинаково
    считали её мёртвой и в панели не висела ссылка-призрак.
    """
    from django.utils import timezone

    link = ProfileFormLink.objects.filter(is_active=True, project=project).first()
    if link is not None and link.is_expired:
        link.is_active = False
        link.deactivated_at = link.deactivated_at or timezone.now()
        link.save(update_fields=['is_active', 'deactivated_at', 'updated_at'])
        return None
    return link


# --- Филиалы -------------------------------------------------------------
# В базе филиал писался импортом как есть («Ошский филиал», «Внешние
# стажёры(Биш)», у части людей пусто), поэтому значение нормализуем:
# сначала смотрим филиал, если он пустой или непонятный — город.

BRANCH_BISHKEK = 'Бишкек'
BRANCH_OSH = 'Ош'
BRANCH_UNKNOWN = 'Без филиала'
BRANCHES = (BRANCH_BISHKEK, BRANCH_OSH, BRANCH_UNKNOWN)


# В данных 1.0 город встречается и латиницей, и строчными («Osh», «Bishkek», «ош»).
OSH_MARKS = ('ош', 'osh')
BISHKEK_MARKS = ('биш', 'bish')


def branch_from_text(*values) -> str:
    """Филиал по первому осмысленному значению: «Ошский филиал» → Ош и т.д."""
    for value in values:
        text = (value or '').lower()
        if not text:
            continue
        if any(mark in text for mark in OSH_MARKS):
            return BRANCH_OSH
        if any(mark in text for mark in BISHKEK_MARKS):
            return BRANCH_BISHKEK
    return BRANCH_UNKNOWN


def branch_of(intern: Intern) -> str:
    """Филиал стажёра одним из трёх значений BRANCHES."""
    return branch_from_text(intern.branch, intern.city)


def branch_filter(branch: str):
    """Q-фильтр по филиалу — та же логика, что и в branch_of."""
    from django.db.models import Q

    def any_of(field, marks):
        q = Q()
        for mark in marks:
            q |= Q(**{f'{field}__icontains': mark})
        return q

    osh = any_of('branch', OSH_MARKS) | (Q(branch='') & any_of('city', OSH_MARKS))
    bishkek = ~osh & (any_of('branch', BISHKEK_MARKS) | (Q(branch='') & any_of('city', BISHKEK_MARKS)))
    if branch == BRANCH_OSH:
        return osh
    if branch == BRANCH_BISHKEK:
        return bishkek
    return ~osh & ~bishkek


def branch_summary(interns) -> dict:
    """Сколько стажёров в каждом филиале: всего, в проекте, без проекта,
    и разбивка по направлениям."""
    from apps.teams.models import TeamMember

    busy_ids = set(
        TeamMember.objects.filter(
            status=TeamMember.Status.ACTIVE, intern__isnull=False,
        ).values_list('intern_id', flat=True),
    )
    cards = {
        name: {'branch': name, 'total': 0, 'busy': 0, 'free': 0}
        for name in BRANCHES
    }
    by_spec: dict[str, dict] = {}
    for intern in interns:
        branch = branch_of(intern)
        card = cards[branch]
        card['total'] += 1
        if intern.pk in busy_ids:
            card['busy'] += 1
        else:
            card['free'] += 1
        spec = str(intern.specialization) if intern.specialization else 'Без направления'
        row = by_spec.setdefault(
            spec, {'name': spec, 'counts': dict.fromkeys(BRANCHES, 0), 'total': 0},
        )
        row['counts'][branch] += 1
        row['total'] += 1

    # филиал, в котором никого нет, не показываем ни карточкой, ни колонкой
    visible = [name for name in BRANCHES if cards[name]['total']]
    rows = sorted(by_spec.values(), key=lambda row: -row['total'])
    for row in rows:
        row['cells'] = [row['counts'][name] for name in visible]
    totals = {
        'total': sum(card['total'] for card in cards.values()),
        'busy': sum(card['busy'] for card in cards.values()),
        'free': sum(card['free'] for card in cards.values()),
        'cells': [cards[name]['total'] for name in visible],
    }
    return {
        'branches': visible,
        'cards': [cards[name] for name in visible],
        'rows': rows,
        'totals': totals,
    }


# --- Архив сотрудников -------------------------------------------------------
# Тимлид (или любой человек из базы), который у нас больше не работает, не
# удаляется: его оценки, табели и история проектов остаются. Он пропадает из
# рабочих списков и снимается со всех текущих проектов. Вход в систему
# закрывается — кроме тех, кто есть в резерве кадров: им логин нужен, чтобы
# самим вести резюме, а проектов и команд после архива у них уже нет.

def archive_person(intern: Intern, user=None, reason: str = '') -> int:
    """Отправить человека в архив. Возвращает, со скольких проектов снят."""
    from django.db import transaction
    from django.utils import timezone

    from apps.audit.services import log as audit_log
    from apps.teams.models import TeamMember

    with transaction.atomic():
        active = intern.team_memberships.filter(status=TeamMember.Status.ACTIVE)
        projects = ', '.join(
            member.project.name for member in active.select_related('project')
            if member.project_id
        )
        closed = active.update(
            status=TeamMember.Status.LEFT, left_at=timezone.localdate(),
        )
        from apps.tasks.workflow import release_stale

        release_stale(intern_ids=[intern.pk], reason='профиль в архиве')
        intern.archive()
        from apps.reserve.models import ReserveCandidate

        in_reserve = ReserveCandidate.objects.filter(
            intern=intern, is_archived=False,
        ).exists()
        if intern.user_id and intern.user.is_active and not in_reserve:
            intern.user.is_active = False
            intern.user.save(update_fields=['is_active'])
        audit_log(
            intern, 'В архив',
            old_value=f'снят с проектов: {projects}' if projects else 'проектов не было',
            reason=reason, user=user,
        )
    return closed


def unarchive_person(intern: Intern, user=None) -> None:
    """Вернуть из архива. На проекты не возвращаем — назначают заново."""
    from django.db import transaction

    from apps.audit.services import log as audit_log

    with transaction.atomic():
        intern.unarchive()
        if intern.user_id and not intern.user.is_active:
            intern.user.is_active = True
            intern.user.save(update_fields=['is_active'])
        audit_log(intern, 'Из архива', user=user)


def pause_person(intern: Intern, user=None) -> bool:
    """Заморозить стажировку: пока статус «Приостановлен», в табеле
    посещаемости и активности его не отмечают (см.
    ``apps.attendance.services.attendance_eligible_members``). Участие
    в командах не трогаем — заморозка не то же самое, что выход. При
    закрытии проекта замороженного не выпускают: его участие закрывается
    с причиной «Стажировка заморожена» (apps.projects.services.release_team).

    Возвращает False, если менять было не с чего (уже не «Активен»).
    """
    from apps.audit.services import log as audit_log

    if intern.status != InternStatus.ACTIVE:
        return False
    intern.status = InternStatus.PAUSED
    intern.save(update_fields=['status', 'updated_at'])
    audit_log(intern, 'Стажировка заморожена', user=user)
    return True


def unpause_person(intern: Intern, user=None) -> bool:
    """Возобновить стажировку после заморозки."""
    from apps.audit.services import log as audit_log

    if intern.status != InternStatus.PAUSED:
        return False
    intern.status = InternStatus.ACTIVE
    intern.save(update_fields=['status', 'updated_at'])
    audit_log(intern, 'Стажировка возобновлена', user=user)
    return True


def approve_resume_bank(intern: Intern, user=None) -> None:
    """Руководитель принял заявку в банк резюме — резюме опубликовано
    (вручную, на geeks.kg), выпускнику уходит уведомление в Telegram."""
    from apps.audit.services import log as audit_log
    from apps.graduate_bot.services import notify_resume_bank_decision

    intern.resume_bank_status = ResumeBankStatus.APPROVED
    intern.resume_bank_comment = ''
    intern.save(update_fields=['resume_bank_status', 'resume_bank_comment', 'updated_at'])
    audit_log(intern, 'Заявка в банк резюме принята', user=user)
    notify_resume_bank_decision(intern, approved=True)


def revise_resume_bank(intern: Intern, comment: str, user=None) -> None:
    """Руководитель отправил заявку на доработку с комментарием —
    комментарий уходит выпускнику в Telegram."""
    from apps.audit.services import log as audit_log
    from apps.graduate_bot.services import notify_resume_bank_decision

    intern.resume_bank_status = ResumeBankStatus.REVISION
    intern.resume_bank_comment = comment
    intern.save(update_fields=['resume_bank_status', 'resume_bank_comment', 'updated_at'])
    audit_log(intern, 'Заявка в банк резюме отправлена на доработку', reason=comment, user=user)
    notify_resume_bank_decision(intern, approved=False, comment=comment)



def join_project_from_form(intern: Intern, link: ProfileFormLink):
    """Анкета заполнена по ссылке проекта — человек сразу в команде.

    Роль — по направлению из анкеты, как при ручном добавлении. Если он
    уже в этой команде, второй раз не добавляем. Тимлиду, выпустившему
    ссылку, — уведомление в портал: пришёл новый человек.
    """
    from django.utils import timezone

    from apps.interns.models import InternStatus
    from apps.notifications.models import NotificationLevel
    from apps.notifications.services import notify
    from apps.teams.forms import ROLE_BY_SPECIALIZATION
    from apps.teams.models import TeamMember, TeamRole

    project = link.project
    if project is None or intern.is_archived:
        return None
    from django.db import transaction

    from apps.teams.rules import check_assignment, lock_and_recheck

    spec = intern.specialization
    role = ROLE_BY_SPECIALIZATION.get(spec.name if spec else '', TeamRole.OTHER)
    if role == TeamRole.PROJECT_MANAGER:
        # Стажёра направления PM на проект назначает только руководитель
        notify(f'Не добавлен(а) в команду «{project.name}»: {intern.full_name}',
               level=NotificationLevel.WARNING, url=intern.get_absolute_url(),
               description='Направление PM: проект назначает руководитель, а не ссылка на анкету.')
        return None
    with transaction.atomic():
        # Блокировка строки проекта: повторная/одновременная отправка анкеты
        # и закрытие набора выполняются строго по очереди (без дублей).
        closed = lock_and_recheck(project, intern, role)
        member = TeamMember.objects.filter(
            project=project, intern=intern, status=TeamMember.Status.ACTIVE,
        ).first()
        joined = member is None
        if joined:
            errors, _warnings = check_assignment(intern, project, role)
            if closed and closed not in errors:
                errors.insert(0, closed)
            if errors:
                # Анкета принята, но в команду не добавляем (набор закрыт
                # или правила состава 2.0) — руководству предупреждение.
                notify(
                    f'Не добавлен(а) в команду «{project.name}»: {intern.full_name}',
                    level=NotificationLevel.WARNING, description=' '.join(errors),
                    url=intern.get_absolute_url(),
                )
                return None
            member = TeamMember.objects.create(
                project=project, intern=intern,
                group=getattr(project, 'group', None),
                role=role,
                status=TeamMember.Status.ACTIVE,
                joined_at=timezone.localdate(),
            )
    update_fields = []
    if intern.status in (InternStatus.WAITING, InternStatus.READY):
        intern.status = InternStatus.ACTIVE
        update_fields.append('status')
    if intern.graduate_status:
        from apps.audit.services import log as audit_log

        audit_log(
            intern, 'Статус выпускника снят',
            old_value=intern.get_graduate_status_display(),
            reason=f'заполнил(а) анкету — в команде «{project.name}»',
        )
        intern.graduate_status = ''
        update_fields.append('graduate_status')
    if update_fields:
        intern.save(update_fields=[*update_fields, 'updated_at'])

    lead = getattr(link.created_by, 'intern_profile', None) if link.created_by_id else None
    if lead is not None:
        notify(
            f'{intern.full_name} заполнил(а) анкету — в команде «{project.name}»'
            if joined else
            f'{intern.full_name} обновил(а) анкету — уже в команде «{project.name}»',
            level=NotificationLevel.INFO, intern=lead,
            description=' · '.join(filter(None, [
                str(intern.specialization or ''), intern.phone, intern.telegram,
            ])),
        )
    return member


def promote_to_lead(intern: Intern, user=None) -> bool:
    """Повысить человека до тимлида — должность, а не роль на проекте.

    Тимлидом он становится сразу на всех своих текущих проектах, а на
    будущих роль подставляется сама (apps.teams.forms). Возвращает False,
    если он уже тимлид.
    """
    from apps.audit.services import log as audit_log
    from apps.teams.models import TeamMember, TeamRole

    if intern.position == Position.TEAM_LEAD:
        return False
    was = intern.get_position_display()
    intern.position = Position.TEAM_LEAD
    intern.save(update_fields=['position', 'updated_at'])
    changed = TeamMember.objects.filter(
        intern=intern, status=TeamMember.Status.ACTIVE,
    ).exclude(role=TeamRole.TEAM_LEAD)
    projects = [member.project.name for member in changed.select_related('project')]
    # Каждого сохраняем по отдельности: на post_save висит занесение
    # тимлида в резерв кадров (apps.reserve.signals).
    for member in changed:
        member.role = TeamRole.TEAM_LEAD
        member.save(update_fields=['role', 'updated_at'])
    audit_log(
        intern, 'Повышен(а) до тимлида', old_value=was, new_value='Тимлид',
        reason='на проектах: ' + (', '.join(projects) or 'пока без проектов'),
        user=user,
    )
    return True


def demote_from_lead(intern: Intern, user=None) -> bool:
    """Снять с должности тимлида — обратно в стажёры.

    На текущих проектах роль возвращается к направлению человека, иначе
    он остался бы тимлидом там, где уже им не является.
    """
    from apps.audit.services import log as audit_log
    from apps.teams.forms import ROLE_BY_SPECIALIZATION
    from apps.teams.models import TeamMember, TeamRole

    if intern.position != Position.TEAM_LEAD:
        return False
    intern.position = Position.INTERN
    intern.save(update_fields=['position', 'updated_at'])
    spec = intern.specialization
    role = ROLE_BY_SPECIALIZATION.get(spec.name if spec else '', TeamRole.OTHER)
    TeamMember.objects.filter(
        intern=intern, status=TeamMember.Status.ACTIVE, role=TeamRole.TEAM_LEAD,
    ).update(role=role)
    audit_log(
        intern, 'Снят(а) с должности тимлида',
        old_value='Тимлид', new_value='Стажёр', user=user,
    )
    return True

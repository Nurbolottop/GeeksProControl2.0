"""Аудит совместимости данных 1.0 с бизнес-логикой 2.0 (этап 11D) — только чтение.

    docker compose … exec -T web python manage.py shell < scripts/compat_audit.py

Печатает только количества и id записей (без ФИО и телефонов). Ничего не меняет.
"""
from collections import Counter

from django.apps import apps
from django.db.models import Count, Q

from apps.accounts.models import User
from apps.interns.models import Intern, Internship, InternStatus, Position
from apps.projects.models import Project
from apps.teams.models import TeamMember, TeamRole
from apps.teams.staff import lead_member_direction

ACTIVE = TeamMember.Status.ACTIVE


def show(title, value, ids=None):
    extra = f'  ids: {sorted(ids)[:25]}' if ids else ''
    print(f'  {title}: {value}{extra}')


print('== 1. Значения вне допустимых вариантов 2.0 (по всем моделям с choices)')
bad_total = 0
for model in apps.get_models():
    if not model._meta.managed or model._meta.proxy:
        continue
    for field in model._meta.concrete_fields:
        if not field.choices:
            continue
        allowed = {c[0] for c in field.flatchoices}
        rows = Counter(model._base_manager.exclude(**{f'{field.attname}__in': list(allowed)})
                       .values_list(field.attname, flat=True))
        rows.pop(None, None) if field.null else None
        if '' in rows and field.blank:
            rows.pop('')
        if rows:
            bad_total += sum(rows.values())
            show(f'{model._meta.label}.{field.name}', dict(rows))
print(f'  итого: {bad_total}')

print('== 2. Пользователи и их карточки')
for role in ('head', 'pm', 'team_lead', 'intern'):
    qs = User.objects.filter(role=role)
    show(f'{role}: всего / активных / с карточкой Intern', f'{qs.count()} / {qs.filter(is_active=True).count()} / '
         f'{qs.filter(intern_profile__isnull=False).count()}')
staff_users = User.objects.filter(role__in=['pm', 'team_lead'], is_active=True)
show('ПМ/тимлиды без карточки Intern (порталы 2.0 их не узнают)',
     staff_users.filter(intern_profile__isnull=True).count(),
     staff_users.filter(intern_profile__isnull=True).values_list('pk', flat=True))

print('== 3. Назначения ПМ и тимлидов на проекты')
mgmt = TeamMember.objects.filter(role__in=[TeamRole.PROJECT_MANAGER, TeamRole.TEAM_LEAD])
show('назначений ПМ/тимлидов всего / активных', f'{mgmt.count()} / {mgmt.filter(status=ACTIVE).count()}')
by_user_only = mgmt.filter(intern__isnull=True)
show('через TeamMember.user без intern (2.0 не видит)', by_user_only.count(), by_user_only.values_list('pk', flat=True))
no_login = mgmt.filter(status=ACTIVE, intern__isnull=False, intern__user__isnull=True)
show('активные, у карточки нет учётки (войти некому)', no_login.count(), no_login.values_list('pk', flat=True))
for role, urole in ((TeamRole.PROJECT_MANAGER, 'pm'), (TeamRole.TEAM_LEAD, 'team_lead')):
    qs = TeamMember.objects.filter(role=role, status=ACTIVE, intern__user__isnull=False)
    show(f'активные {role}: роль учётки', dict(Counter(qs.values_list('intern__user__role', flat=True))))
    show(f'активные {role}: учётка отключена', qs.filter(intern__user__is_active=False).count(),
         qs.filter(intern__user__is_active=False).values_list('pk', flat=True))
leads = TeamMember.objects.filter(role=TeamRole.TEAM_LEAD, status=ACTIVE).select_related('intern__specialization')
no_dir = [m.pk for m in leads if not lead_member_direction(m)]
show('активные тимлиды без направления (явного или по специализации)', len(no_dir), no_dir)
show('активные тимлиды: lead_direction пусто (берётся по специализации)', leads.filter(lead_direction='').count())
users_with_portal = {
    'pm': User.objects.filter(role='pm', is_active=True).exclude(
        intern_profile__team_memberships__role=TeamRole.PROJECT_MANAGER).count(),
    'team_lead': User.objects.filter(role='team_lead', is_active=True).exclude(
        intern_profile__team_memberships__role=TeamRole.TEAM_LEAD).count(),
}
show('учётки ПМ/тимлида без единого назначения своей роли (портал пуст)', users_with_portal)

print('== 4. Кадровые поля 2.0 (меню бота, списки сотрудников)')
lead_people = Intern.objects.filter(Q(position=Position.TEAM_LEAD) | Q(user__role='team_lead'))
show('тимлиды: position=team_lead / user.role=team_lead',
     f'{Intern.objects.filter(position=Position.TEAM_LEAD).count()} / {Intern.objects.filter(user__role="team_lead").count()}')
show('тимлиды (по учётке) с position != team_lead', Intern.objects.filter(user__role='team_lead').exclude(
    position=Position.TEAM_LEAD).count(), Intern.objects.filter(user__role='team_lead').exclude(
    position=Position.TEAM_LEAD).values_list('pk', flat=True))
show('тимлиды/ПМ: staff_status', dict(Counter(Intern.objects.filter(user__role__in=['pm', 'team_lead'])
                                             .values_list('user__role', 'staff_status'))))
show('тимлиды/ПМ: staff_role', dict(Counter(Intern.objects.filter(user__role__in=['pm', 'team_lead'])
                                           .values_list('user__role', 'staff_role'))))
show('Intern.team_lead (связь 1.0 «тимлид стажёра») заполнено', Intern.objects.filter(team_lead__isnull=False).count())

print('== 5. Стажёры')
show('по статусам', dict(Counter(Intern.objects.values_list('status', flat=True))))
show('архивных', Intern.objects.filter(is_archived=True).count() if hasattr(Intern, 'is_archived') else '—')
show('без города (city и branch пусты)', Intern.objects.filter(city='', branch='').count())
show('city пуст, branch заполнен', Intern.objects.filter(city='').exclude(branch='').count())
show('без направления', Intern.objects.filter(specialization__isnull=True).count())
show('graduate_status', dict(Counter(Intern.objects.values_list('graduate_status', flat=True))))
multi = (TeamMember.objects.filter(status=ACTIVE, intern__isnull=False).exclude(
    role__in=[TeamRole.PROJECT_MANAGER, TeamRole.TEAM_LEAD]).values('intern').annotate(n=Count('project', distinct=True))
         .filter(n__gt=1))
show('в нескольких проектах одновременно (активно, не ПМ/тимлид)', multi.count(), [r['intern'] for r in multi])
for status in (InternStatus.PAUSED, InternStatus.EXPELLED, InternStatus.CANCELLED):
    qs = TeamMember.objects.filter(status=ACTIVE, intern__status=status)
    show(f'статус {status}, но активное участие', qs.count(), qs.values_list('intern_id', flat=True))
closed = TeamMember.objects.filter(status=ACTIVE, project__status__in=['completed', 'cancelled'])
show('активные участия в завершённых/отменённых проектах', closed.count(), closed.values_list('project_id', flat=True))
show('выход без причины', TeamMember.objects.filter(status=TeamMember.Status.LEFT, left_reason='').count())
show('выход без даты', TeamMember.objects.filter(status=TeamMember.Status.LEFT, left_at__isnull=True).count())

print('== 6. Периоды стажировки (Internship)')
show('периодов всего / открытых', f'{Internship.objects.count()} / {Internship.objects.filter(ended_on__isnull=True, end_reason="").count()}')
show('по источнику', dict(Counter(Internship.objects.values_list('source', flat=True))))
from apps.teams.selectors import lead_intern_ids  # noqa: E402
nolead = Intern.objects.exclude(pk__in=lead_intern_ids())
show('стажёров (не тимлидов) без периода', nolead.filter(internships__isnull=True).count(),
     nolead.filter(internships__isnull=True).values_list('pk', flat=True))
show('периодов без даты начала', Internship.objects.filter(started_on__isnull=True).count())

print('== 7. Проекты')
show('по статусам', dict(Counter(Project.objects.values_list('status', flat=True))))
show('набор открыт (recruitment_open) у активных', f'{Project.objects.filter(status="active", recruitment_open=True).count()} '
     f'из {Project.objects.filter(status="active").count()}')
show('без ПМ (активного)', Project.objects.filter(status='active').exclude(
    team_members__role=TeamRole.PROJECT_MANAGER, team_members__status=ACTIVE).count())
show('без тимлида (активного)', Project.objects.filter(status='active').exclude(
    team_members__role=TeamRole.TEAM_LEAD, team_members__status=ACTIVE).count())
show('без заказчика', Project.objects.filter(client__isnull=True).count())

print('== 8. Задачи, документы, отчёты, оценки, посещаемость')
Task = apps.get_model('tasks', 'Task')
TaskAssignment = apps.get_model('tasks', 'TaskAssignment')
show('задач / с исполнителем 1.0 / назначений 2.0', f'{Task.objects.count()} / '
     f'{Task.objects.filter(assignee__isnull=False).count()} / {TaskAssignment.objects.count()}')
lost = Task.objects.filter(assignee__isnull=False, assignments__isnull=True)
show('исполнитель 1.0 не перенесён в назначения', lost.count(), lost.values_list('pk', flat=True))
show('задачи без проекта', Task.objects.filter(project__isnull=True).count())
Document = apps.get_model('documents', 'Document')
show('документов / без файла / title пуст', f'{Document.objects.count()} / {Document.objects.filter(file="").count()} / '
     f'{Document.objects.filter(Q(title__isnull=True) | Q(title="")).count()}')
for label in ('reports.WeeklyReport', 'reports.LeadWeeklyReport', 'reports.PmWeeklyReport', 'reports.WeeklyEvaluation',
              'interns.InternEvaluation', 'attendance.GroupMeeting', 'audit.AuditLog', 'reserve.ReserveCandidate',
              'tgbot.TelegramUpdate', 'tgbot.OutboundMessage', 'notifications.Notification'):
    try:
        show(label, apps.get_model(label).objects.count())
    except LookupError:
        show(label, 'нет модели')
for label in [m._meta.label for m in apps.get_models() if m._meta.app_label in ('reports', 'attendance', 'projects')
              and ('Report' in m.__name__ or 'Score' in m.__name__ or 'Mark' in m.__name__)]:
    show(label, apps.get_model(label).objects.count())

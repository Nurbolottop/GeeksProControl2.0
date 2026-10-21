"""Сверка старых заморозок с правилом 2.0 (UI-03C).

До 2.0 заморозка не закрывала участия: в базе могут быть «Приостановленные»
стажёры, которые всё ещё числятся активными в командах. По умолчанию
команда только показывает такие записи и ничего не меняет.

  manage.py freeze_reconciliation           — отчёт
  manage.py freeze_reconciliation --apply   — закрыть такие участия

С --apply участие закрывается с причиной «Стажировка заморожена» датой
заморозки из журнала (если её нет — сегодняшней, это видно в отчёте),
незавершённые задачи возвращаются «К выполнению», каждое изменение
пишется в журнал с пометкой «сверка». Исторические записи (журнал,
оценки, задачи, вышедшие участия) не меняются. Повторный запуск ничего
не делает.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone


class Command(BaseCommand):
    help = 'Сверка заморозок: замороженные стажёры с активными участиями.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Закрыть найденные участия.')

    def handle(self, *args, apply=False, **options):
        from apps.audit.services import log as audit_log
        from apps.dashboard.graduation import _freeze_events
        from apps.interns.freeze import UNFINISHED_TASKS
        from apps.interns.models import Intern, InternStatus
        from apps.tasks.models import Task, TaskStatus
        from apps.teams.models import TeamMember

        stale = (TeamMember.objects.filter(status=TeamMember.Status.ACTIVE, intern__status=InternStatus.PAUSED)
                 .select_related('intern', 'project').order_by('intern__full_name', 'pk'))
        rows = list(stale)
        if not rows:
            self.stdout.write('Расхождений нет: у замороженных стажёров нет активных участий.')
            return
        events = _freeze_events({m.intern_id for m in rows})
        self.stdout.write(f'Замороженные с активными участиями: {len(rows)}')
        changed = 0
        for member in rows:
            frozen = [day for day, is_freeze in events.get(member.intern_id, []) if is_freeze]
            day = frozen[-1] if frozen else None
            project = member.project.name if member.project_id else '—'
            self.stdout.write(f'  {member.intern.full_name} · {project} · заморожен '
                              f'{day:%d.%m.%Y}' if day else f'  {member.intern.full_name} · {project} · дата заморозки неизвестна')
            if not apply:
                continue
            with transaction.atomic():
                locked = TeamMember.objects.select_for_update().filter(pk=member.pk, status=TeamMember.Status.ACTIVE).first()
                intern = Intern.objects.select_for_update().get(pk=member.intern_id)
                if locked is None or intern.status != InternStatus.PAUSED:
                    continue
                left_at = day or timezone.localdate()
                if locked.joined_at and left_at < locked.joined_at:
                    left_at = locked.joined_at
                TeamMember.objects.filter(pk=locked.pk).update(
                    status=TeamMember.Status.LEFT, left_at=left_at, left_reason=TeamMember.LeftReason.FROZEN)
                tasks = list(Task.objects.select_for_update(of=('self',)).filter(
                    assignee_id=intern.user_id, project_id=locked.project_id, status__in=UNFINISHED_TASKS,
                    assignments__isnull=True,
                )) if intern.user_id and locked.project_id else []
                for task in tasks:
                    audit_log(task, 'Возвращена «К выполнению»', old_value=task.get_status_display(),
                              new_value=TaskStatus.NEW.label, reason='сверка заморозки')
                Task.objects.filter(pk__in=[t.pk for t in tasks]).update(status=TaskStatus.NEW)
                from apps.tasks.workflow import release_stale

                release_stale(intern_ids=[intern.pk], project_ids=[locked.project_id], reason='сверка заморозки')
                audit_log(intern, 'Сверка заморозки: участие закрыто', new_value=project,
                          reason=f'заморожен {left_at:%d.%m.%Y}' + ('' if day else ' (дата неизвестна — сегодня)'))
                changed += 1
        if apply:
            self.stdout.write(self.style.SUCCESS(f'Закрыто участий: {changed}'))
        else:
            self.stdout.write('Ничего не изменено. Чтобы закрыть участия: --apply')

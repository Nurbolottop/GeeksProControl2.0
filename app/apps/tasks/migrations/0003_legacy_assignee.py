"""Перенос прежнего единственного исполнителя (Task.assignee) в TaskAssignment.

Только добавляет записи: статус задачи, автор, исполнитель (поле assignee)
и даты не меняются. Перенос — только если у учётки есть профиль стажёра.
Незавершённое назначение без активного участия в проекте создаётся
закрытым — чтобы не оставлять активное назначение бывшему участнику.
"""
from django.db import migrations
from django.utils import timezone

STATES = {'new', 'in_progress', 'review', 'done'}


def forward(apps, schema_editor):
    Task = apps.get_model('tasks', 'Task')
    Intern = apps.get_model('interns', 'Intern')
    TeamMember = apps.get_model('teams', 'TeamMember')
    TaskAssignment = apps.get_model('tasks', 'TaskAssignment')
    TaskEvent = apps.get_model('tasks', 'TaskEvent')
    now = timezone.now()
    tasks = Task.objects.filter(project__isnull=False, assignee__isnull=False).exclude(assignments__isnull=False)
    for task in tasks.iterator():
        intern = Intern.objects.filter(user_id=task.assignee_id).first()
        if intern is None:
            continue
        member = TeamMember.objects.filter(project_id=task.project_id, intern=intern, status='active').first()
        status = task.status if task.status in STATES else 'new'
        active = status == 'done' or member is not None
        assignment = TaskAssignment.objects.create(
            task=task, intern=intern, person_name=intern.full_name, role=member.role if member else '',
            status=status, is_active=active, released_at=None if active else now,
            release_reason='' if active else 'нет активного участия в проекте (перенос старой задачи)',
        )
        TaskEvent.objects.create(task=task, kind='assigned', assignment=assignment, new_value=intern.full_name,
                                 text='Перенесено из прежнего поля «Исполнитель»')


class Migration(migrations.Migration):
    dependencies = [
        ('tasks', '0002_task_board'),
        ('interns', '0024_staff_status'),
        ('teams', '0007_leftreason_staff'),
    ]

    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]

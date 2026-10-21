"""Группы потока: один состав — один проект."""
from django.db import migrations


def create_groups(apps, schema_editor):
    """Для каждого проекта с потоком создаёт группу и переносит в неё команду."""
    Project = apps.get_model('projects', 'Project')
    Group = apps.get_model('flows', 'Group')
    TeamMember = apps.get_model('teams', 'TeamMember')

    for project in Project.objects.exclude(flow__isnull=True).order_by(
        'flow__number', 'number_in_flow', 'pk',
    ):
        number = project.number_in_flow or (
            Group.objects.filter(flow=project.flow).count() + 1
        )
        group, _ = Group.objects.get_or_create(
            flow=project.flow, number=number,
            defaults={'project': project},
        )
        if group.project_id != project.pk:
            group.project = project
            group.save(update_fields=['project'])
        TeamMember.objects.filter(project=project).update(group=group)


class Migration(migrations.Migration):
    dependencies = [
        ('flows', '0002_group'),
        ('teams', '0002_teammember_group_alter_teammember_project'),
    ]

    operations = [
        migrations.RunPython(create_groups, migrations.RunPython.noop),
    ]

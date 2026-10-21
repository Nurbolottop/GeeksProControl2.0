"""Приводит статусы этапов к трём: не начат / в процессе / завершён."""
from django.db import migrations


def collapse_statuses(apps, schema_editor):
    ProjectStage = apps.get_model('projects', 'ProjectStage')
    ProjectStage.objects.filter(status__in=['review', 'blocked']).update(
        status='in_progress',
    )


class Migration(migrations.Migration):
    dependencies = [
        ('projects', '0006_stage_order_and_staging'),
    ]

    operations = [
        migrations.RunPython(collapse_statuses, migrations.RunPython.noop),
    ]

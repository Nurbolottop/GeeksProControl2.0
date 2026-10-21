"""Переносит «Поток N.» из комментария руководителя в поле flow."""
import re

from django.db import migrations


def backfill_flow(apps, schema_editor):
    Project = apps.get_model('projects', 'Project')
    pattern = re.compile(r'^Поток (\d+)\.?\s*(.*)$', re.S)
    for project in Project.objects.all():
        match = pattern.match(project.head_comment or '')
        if match:
            project.flow = int(match.group(1))
            project.head_comment = match.group(2).strip()
            project.save(update_fields=['flow', 'head_comment'])


class Migration(migrations.Migration):
    dependencies = [
        ('projects', '0002_project_flow'),
    ]

    operations = [
        migrations.RunPython(backfill_flow, migrations.RunPython.noop),
    ]

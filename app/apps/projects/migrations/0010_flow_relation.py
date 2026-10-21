"""Поток становится отдельной сущностью, проекты получают номер вида 13.1."""
import django.db.models.deletion
from django.db import migrations, models


def create_flows(apps, schema_editor):
    Flow = apps.get_model('flows', 'Flow')
    Project = apps.get_model('projects', 'Project')

    numbers = sorted(
        Project.objects.exclude(legacy_flow_number__isnull=True)
        .values_list('legacy_flow_number', flat=True).distinct(),
    )
    flows = {
        number: Flow.objects.get_or_create(
            number=number,
            defaults={'status': 'active' if number >= 13 else 'finished'},
        )[0]
        for number in numbers
    }
    for number, flow in flows.items():
        projects = Project.objects.filter(
            legacy_flow_number=number,
        ).order_by('pk')
        for index, project in enumerate(projects, 1):
            project.flow = flow
            project.number_in_flow = index
            project.save(update_fields=['flow', 'number_in_flow'])


class Migration(migrations.Migration):
    dependencies = [
        ('projects', '0009_split_development_stage'),
        ('flows', '0001_initial'),
    ]

    operations = [
        migrations.RenameField(
            model_name='project', old_name='flow',
            new_name='legacy_flow_number',
        ),
        migrations.AddField(
            model_name='project',
            name='flow',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='projects', to='flows.flow',
                verbose_name='Поток',
            ),
        ),
        migrations.AddField(
            model_name='project',
            name='number_in_flow',
            field=models.PositiveSmallIntegerField(
                blank=True, null=True,
                help_text='Например, 1 — тогда ID проекта будет 13.1',
                verbose_name='Номер в потоке',
            ),
        ),
        migrations.RunPython(create_flows, migrations.RunPython.noop),
        migrations.RemoveField(model_name='project', name='legacy_flow_number'),
    ]

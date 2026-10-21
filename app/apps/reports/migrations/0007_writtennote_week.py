"""Запись отчёта привязана к неделе."""
import datetime

from django.db import migrations, models


def fill_weeks(apps, schema_editor):
    WrittenNote = apps.get_model('reports', 'WrittenNote')
    for note in WrittenNote.objects.all():
        day = note.date
        WrittenNote.objects.filter(pk=note.pk).update(
            week_start=day - datetime.timedelta(days=day.weekday()),
        )


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0006_writtennote'),
    ]

    operations = [
        migrations.AddField(
            model_name='writtennote',
            name='week_start',
            field=models.DateField(
                db_index=True, null=True, verbose_name='Неделя',
            ),
        ),
        migrations.RunPython(fill_weeks, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='writtennote',
            name='week_start',
            field=models.DateField(db_index=True, verbose_name='Неделя'),
        ),
        migrations.AlterModelOptions(
            name='writtennote',
            options={
                'ordering': ['-week_start', '-created_at'],
                'verbose_name': 'Запись отчёта',
                'verbose_name_plural': 'Записи отчёта',
            },
        ),
    ]

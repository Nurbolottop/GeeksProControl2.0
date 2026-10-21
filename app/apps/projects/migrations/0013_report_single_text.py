"""Отчёт по проекту — одно текстовое поле, дата ставится сама."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0012_projectreport'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectreport',
            name='text',
            field=models.TextField(default='', verbose_name='Отчёт'),
            preserve_default=False,
        ),
        migrations.RemoveField(model_name='projectreport', name='status'),
        migrations.RemoveField(model_name='projectreport', name='done'),
        migrations.RemoveField(model_name='projectreport', name='next_steps'),
        migrations.RemoveField(model_name='projectreport', name='notes'),
        migrations.AlterField(
            model_name='projectreport',
            name='date',
            field=models.DateField(
                auto_now_add=True, db_index=True, verbose_name='Дата отчёта',
            ),
        ),
    ]

"""Написанная руками часть недельного отчёта."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0002_monthlyreport'),
    ]

    operations = [
        migrations.AddField(
            model_name='weeklyreport',
            name='done',
            field=models.TextField(blank=True, verbose_name='Что сделано за неделю'),
        ),
        migrations.AddField(
            model_name='weeklyreport',
            name='issues',
            field=models.TextField(blank=True, verbose_name='Проблемы и решения'),
        ),
        migrations.AddField(
            model_name='weeklyreport',
            name='next_steps',
            field=models.TextField(blank=True, verbose_name='Что предстоит'),
        ),
    ]

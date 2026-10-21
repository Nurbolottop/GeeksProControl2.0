"""Текстовые части недельного отчёта убраны — остаются только цифры."""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0003_weeklyreport_written_parts'),
    ]

    operations = [
        migrations.RemoveField(model_name='weeklyreport', name='done'),
        migrations.RemoveField(model_name='weeklyreport', name='next_steps'),
        migrations.RemoveField(model_name='weeklyreport', name='issues'),
    ]

"""Письменный отчёт: проблемы и достижения."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0004_remove_written_parts'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='WrittenReport',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False,
                    verbose_name='ID',
                )),
                ('created_at', models.DateTimeField(
                    auto_now_add=True, verbose_name='Создано',
                )),
                ('updated_at', models.DateTimeField(
                    auto_now=True, verbose_name='Изменено',
                )),
                ('date', models.DateField(
                    auto_now_add=True, db_index=True, verbose_name='Дата',
                )),
                ('problems', models.TextField(blank=True, verbose_name='Проблемы')),
                ('achievements', models.TextField(
                    blank=True, verbose_name='Достижения',
                )),
                ('author', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+', to=settings.AUTH_USER_MODEL,
                    verbose_name='Автор',
                )),
            ],
            options={
                'verbose_name': 'Письменный отчёт',
                'verbose_name_plural': 'Письменные отчёты',
                'ordering': ['-date', '-created_at'],
            },
        ),
    ]

"""Отчёты по проекту, которые пишутся руками."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0011_remove_project_project_manager_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProjectReport',
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
                    db_index=True, verbose_name='Дата отчёта',
                )),
                ('status', models.CharField(
                    blank=True, max_length=255,
                    help_text='Например: стадия завершения, идёт финальное тестирование',
                    verbose_name='Текущее положение',
                )),
                ('done', models.TextField(blank=True, verbose_name='Выполнено')),
                ('next_steps', models.TextField(
                    blank=True, verbose_name='Предстоит',
                )),
                ('notes', models.TextField(
                    blank=True, verbose_name='Проблемы и заметки',
                )),
                ('author', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+', to=settings.AUTH_USER_MODEL,
                    verbose_name='Автор',
                )),
                ('project', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='reports', to='projects.project',
                    verbose_name='Проект',
                )),
            ],
            options={
                'verbose_name': 'Отчёт по проекту',
                'verbose_name_plural': 'Отчёты по проектам',
                'ordering': ['-date', '-created_at'],
            },
        ),
    ]

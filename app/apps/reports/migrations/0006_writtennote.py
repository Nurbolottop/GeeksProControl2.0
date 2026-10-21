"""Записи руководителя по одной: достижение, проблема, вопрос."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0005_writtenreport'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='WrittenNote',
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
                ('kind', models.CharField(
                    choices=[
                        ('achievement', 'Достижение'),
                        ('problem', 'Проблема'),
                        ('question', 'Вопрос'),
                    ],
                    db_index=True, default='achievement', max_length=20,
                    verbose_name='Раздел',
                )),
                ('text', models.TextField(verbose_name='Текст')),
                ('date', models.DateField(
                    auto_now_add=True, db_index=True, verbose_name='Дата',
                )),
                ('author', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+', to=settings.AUTH_USER_MODEL,
                    verbose_name='Автор',
                )),
            ],
            options={
                'verbose_name': 'Запись отчёта',
                'verbose_name_plural': 'Записи отчёта',
                'ordering': ['-date', '-created_at'],
            },
        ),
        migrations.DeleteModel(name='WrittenReport'),
    ]

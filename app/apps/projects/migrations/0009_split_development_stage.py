"""Разделяет «Разработку» на Backend и Frontend / Мобильную разработку.

Набор этапов зависит от типа проекта: у мобильных приложений вместо
Frontend идёт «Мобильная разработка».
"""
from django.db import migrations, models

BEFORE = ['new', 'documents', 'requirements', 'team_forming', 'design']
AFTER = ['staging', 'testing', 'rework', 'delivery', 'production', 'completed']

MOBILE_TYPE_HINTS = ('mobile', 'моб')


def split_development(apps, schema_editor):
    ProjectType = apps.get_model('projects', 'ProjectType')
    Project = apps.get_model('projects', 'Project')
    ProjectStage = apps.get_model('projects', 'ProjectStage')

    # Отмечаем мобильные типы проектов
    for project_type in ProjectType.objects.all():
        name = project_type.name.lower()
        if any(hint in name for hint in MOBILE_TYPE_HINTS):
            project_type.is_mobile = True
            project_type.save(update_fields=['is_mobile'])

    for project in Project.objects.select_related('project_type').all():
        is_mobile = bool(project.project_type and project.project_type.is_mobile)
        dev_stages = ['backend', 'mobile_dev' if is_mobile else 'frontend']
        order_keys = [*BEFORE, *dev_stages, *AFTER]

        stages = {stage.key: stage for stage in project.stages.all()}
        # Старую «Разработку» превращаем в Backend
        development = stages.pop('development', None)
        if development is not None:
            if 'backend' in stages:
                development.delete()
            else:
                development.key = 'backend'
                development.save(update_fields=['key'])
                stages['backend'] = development

        for index, key in enumerate(order_keys):
            stage = stages.get(key)
            if stage is None:
                ProjectStage.objects.create(
                    project=project, key=key, order=index,
                    status='not_started', progress=0,
                )
            elif stage.order != index:
                stage.order = index
                stage.save(update_fields=['order'])

        # Этапы, которых нет в наборе типа проекта (например, frontend
        # у мобильного приложения), убираем, если они не начаты
        ProjectStage.objects.filter(project=project).exclude(
            key__in=order_keys,
        ).filter(status='not_started').delete()

        if project.current_stage == 'development':
            project.current_stage = 'backend'
            project.save(update_fields=['current_stage'])


class Migration(migrations.Migration):
    dependencies = [
        ('projects', '0008_alter_projectstage_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='projecttype',
            name='is_mobile',
            field=models.BooleanField(
                default=False,
                help_text='У таких проектов этап Frontend заменяется на «Мобильная разработка»',
                verbose_name='Мобильная разработка',
            ),
        ),
        migrations.AlterField(
            model_name='project',
            name='current_stage',
            field=models.CharField(
                choices=[
                    ('new', 'Новый'), ('documents', 'Документы'),
                    ('requirements', 'ТЗ'),
                    ('team_forming', 'Формирование команды'),
                    ('design', 'Дизайн'), ('backend', 'Backend'),
                    ('frontend', 'Frontend'),
                    ('mobile_dev', 'Мобильная разработка'),
                    ('staging', 'Тестовый сервер'),
                    ('testing', 'Тестирование'), ('rework', 'Доработка'),
                    ('delivery', 'Сдача'), ('production', 'Продакшен'),
                    ('completed', 'Завершён'),
                ],
                db_index=True, default='new', max_length=20,
                verbose_name='Текущий этап',
            ),
        ),
        migrations.AlterField(
            model_name='projectstage',
            name='key',
            field=models.CharField(
                choices=[
                    ('new', 'Новый'), ('documents', 'Документы'),
                    ('requirements', 'ТЗ'),
                    ('team_forming', 'Формирование команды'),
                    ('design', 'Дизайн'), ('backend', 'Backend'),
                    ('frontend', 'Frontend'),
                    ('mobile_dev', 'Мобильная разработка'),
                    ('staging', 'Тестовый сервер'),
                    ('testing', 'Тестирование'), ('rework', 'Доработка'),
                    ('delivery', 'Сдача'), ('production', 'Продакшен'),
                    ('completed', 'Завершён'),
                ],
                max_length=20, verbose_name='Этап',
            ),
        ),
        migrations.RunPython(split_development, migrations.RunPython.noop),
    ]

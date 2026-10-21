"""Добавляет этап «Тестовый сервер» и переставляет Сдачу перед Продакшеном.

Новый порядок: … Разработка → Тестовый сервер → Тестирование → Доработка
→ Сдача → Продакшен → Завершён.
"""
from django.db import migrations

NEW_ORDER = [
    'new', 'documents', 'requirements', 'team_forming', 'design',
    'development', 'staging', 'testing', 'rework', 'delivery',
    'production', 'completed',
]


def apply_new_order(apps, schema_editor):
    Project = apps.get_model('projects', 'Project')
    ProjectStage = apps.get_model('projects', 'ProjectStage')

    for project in Project.objects.all():
        current_index = (
            NEW_ORDER.index(project.current_stage)
            if project.current_stage in NEW_ORDER else 0
        )
        existing = {stage.key: stage for stage in project.stages.all()}
        for index, key in enumerate(NEW_ORDER):
            stage = existing.get(key)
            # Этапы до текущего считаем пройденными
            passed = index < current_index or project.status == 'completed'
            if stage is None:
                ProjectStage.objects.create(
                    project=project, key=key, order=index,
                    status='done' if passed else 'not_started',
                    progress=100 if passed else 0,
                )
            elif stage.order != index:
                stage.order = index
                stage.save(update_fields=['order'])


class Migration(migrations.Migration):
    dependencies = [
        ('projects', '0005_alter_project_current_stage_alter_projectstage_key'),
    ]

    operations = [
        migrations.RunPython(apply_new_order, migrations.RunPython.noop),
    ]

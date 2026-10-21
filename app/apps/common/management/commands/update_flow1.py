"""Обновляет этапы, прогресс и комментарии проектов первого потока.

    python manage.py update_flow1
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.projects.models import (
    Project,
    ProjectStage,
    ProjectStageKey,
    ProjectStatus,
    ProjectType,
    lifecycle_stages,
)

# название → (этап, прогресс, комментарий, мобильный ли проект)
UPDATES = {
    'Омур': (ProjectStageKey.DELIVERY, 95, 'На стадии выдачи.', False),
    'ОБА': (ProjectStageKey.REWORK, 85, 'Тестирование и доработки.', False),
    'Балажан': (ProjectStageKey.DELIVERY, 95,
                'Стадия выдачи: организовать и сдать.', False),
    'БилимОрдо': (ProjectStageKey.REWORK, 85,
                  'Пришли задачи после тестирования, идёт доработка.', False),
    'Umai': (ProjectStageKey.PRODUCTION, 90,
             'Проблемы с загрузкой в App Store.', True),
    'Биклин': (ProjectStageKey.BACKEND, 60,
               'Проблемы с OTP, нужно залить приложение.', True),
    'Олимпийская школа': (ProjectStageKey.BACKEND, 40,
                          'Backend и frontend начали работу.', False),
    'Медресе': (ProjectStageKey.BACKEND, 45,
                'Дизайн утверждён, идёт разработка backend и frontend.', False),
    'Вистайл': (ProjectStageKey.REWORK, 85, 'Тестирование и доработки.', False),
    'Учкун': (ProjectStageKey.NEW, 0, 'Не начался.', False),
    'Агартуу': (ProjectStageKey.NEW, 0, 'Не начался.', False),
    'ВФК': (ProjectStageKey.DESIGN, 30, 'Сдают дизайн.', False),
    'Энактус': (ProjectStageKey.DELIVERY, 95, 'Нужно сдать.', False),
    'ПроМонтаж': (ProjectStageKey.PRODUCTION, 90,
                  'Нужно купить прод-сервер и закончить.', False),
    'Туризм': (ProjectStageKey.DESIGN, 25, 'Дизайн: идут правки.', False),
}


class Command(BaseCommand):
    help = 'Обновляет статусы проектов первого потока'

    @transaction.atomic
    def handle(self, *args, **options):
        mobile_type, _ = ProjectType.objects.get_or_create(
            name='Mobile App', defaults={'is_mobile': True},
        )
        if not mobile_type.is_mobile:
            mobile_type.is_mobile = True
            mobile_type.save(update_fields=['is_mobile'])

        updated = 0
        for name, (stage, progress, comment, is_mobile) in UPDATES.items():
            project = Project.objects.filter(name=name).first()
            if project is None:
                self.stdout.write(self.style.WARNING(f'Не найден: {name}'))
                continue

            if is_mobile and project.project_type_id != mobile_type.pk:
                project.project_type = mobile_type
                # У мобильного проекта другой набор этапов
                project.stages.all().delete()
                ProjectStage.objects.bulk_create([
                    ProjectStage(project=project, key=key, order=index)
                    for index, key in enumerate(lifecycle_stages(mobile_type))
                ])

            project.current_stage = stage
            project.progress = progress
            project.head_comment = comment
            project.status = ProjectStatus.ACTIVE
            project.actual_end_date = None
            project.save()

            # Этапы до текущего помечаем завершёнными
            order = lifecycle_stages(project.project_type)
            current_index = order.index(stage) if stage in order else 0
            for project_stage in project.stages.all():
                index = order.index(project_stage.key) if project_stage.key in order else 0
                if index < current_index:
                    project_stage.status = ProjectStage.Status.DONE
                elif index == current_index:
                    project_stage.status = ProjectStage.Status.IN_PROGRESS
                else:
                    project_stage.status = ProjectStage.Status.NOT_STARTED
                project_stage.save(update_fields=['status'])
            updated += 1

        self.stdout.write(self.style.SUCCESS(f'Обновлено проектов: {updated}.'))

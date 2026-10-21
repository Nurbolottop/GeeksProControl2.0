"""Создаёт первый поток с текущими проектами GeeksPro.

Статусы, даты и этапы взяты из рабочей таблицы (лист «проекты»).
Для проектов, которых в таблице не было, ставится «Новый».

    python manage.py seed_flow1
"""
import datetime

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.clients.models import Client
from apps.flows.models import Flow, Group
from apps.projects.models import (
    Project,
    ProjectStageKey,
    ProjectStatus,
    ProjectType,
)
from apps.projects.services import create_project

D = datetime.date
WEB = 'Web-сайт'
MOBILE = 'Mobile App'

# (название, город, тип, статус, этап, прогресс, договор, дедлайн, факт. завершение)
PROJECTS = [
    ('Омур', 'Ош', WEB, 'active', ProjectStageKey.TESTING, 75,
     D(2026, 4, 14), D(2026, 7, 13), None),
    ('ОБА', 'Ош', WEB, 'active', ProjectStageKey.BACKEND, 65,
     D(2026, 3, 1), D(2026, 7, 3), None),
    ('Балажан', 'Бишкек', WEB, 'active', ProjectStageKey.NEW, 0, None, None, None),
    ('БилимОрдо', 'Бишкек', WEB, 'active', ProjectStageKey.NEW, 0, None, None, None),
    ('Umai', 'Бишкек', WEB, 'active', ProjectStageKey.BACKEND, 40, None, None, None),
    ('Биклин', 'Бишкек', MOBILE, 'active', ProjectStageKey.TESTING, 80,
     None, D(2026, 7, 31), None),
    ('Олимпийская школа', 'Бишкек', WEB, 'active', ProjectStageKey.BACKEND, 60,
     D(2026, 5, 18), D(2026, 8, 16), None),
    ('Медресе', 'Ош', WEB, 'active', ProjectStageKey.BACKEND, 60,
     D(2026, 5, 19), D(2026, 8, 17), None),
    ('Вистайл', 'Бишкек', WEB, 'active', ProjectStageKey.PRODUCTION, 90,
     D(2026, 1, 5), D(2026, 4, 30), None),
    ('Учкун', 'Бишкек', WEB, 'active', ProjectStageKey.DESIGN, 25, None, None, None),
    ('Агартуу', 'Бишкек', WEB, 'active', ProjectStageKey.BACKEND, 45,
     D(2026, 6, 23), D(2026, 9, 21), None),
    ('ВФК', 'Бишкек', WEB, 'active', ProjectStageKey.NEW, 0, None, None, None),
    ('Энактус', 'Бишкек', WEB, 'completed', ProjectStageKey.COMPLETED, 100,
     D(2026, 3, 9), D(2026, 5, 6), D(2026, 5, 15)),
    ('ПроМонтаж', 'Бишкек', WEB, 'completed', ProjectStageKey.COMPLETED, 100,
     D(2026, 3, 9), D(2026, 5, 30), D(2026, 5, 30)),
    ('Туризм', 'Бишкек', WEB, 'active', ProjectStageKey.NEW, 0, None, None, None),
]

STATUS_MAP = {
    'active': ProjectStatus.ACTIVE,
    'completed': ProjectStatus.COMPLETED,
}


class Command(BaseCommand):
    help = 'Создаёт первый поток и его проекты'

    @transaction.atomic
    def handle(self, *args, **options):
        flow, _ = Flow.objects.get_or_create(
            number=1, defaults={'status': Flow.Status.ACTIVE},
        )
        types = {
            name: ProjectType.objects.get_or_create(
                name=name, defaults={'is_mobile': name == MOBILE},
            )[0]
            for name in (WEB, MOBILE)
        }

        created = 0
        for index, (name, city, type_name, status, stage, progress,
                    contract, deadline, done) in enumerate(PROJECTS, 1):
            if Project.objects.filter(name=name, flow=flow).exists():
                continue
            client, _ = Client.objects.get_or_create(
                organization=name, defaults={'city': city},
            )
            project = Project(
                name=name,
                client=client,
                city=city,
                project_type=types[type_name],
                status=STATUS_MAP[status],
                current_stage=stage,
                progress=progress,
                contract_date=contract,
                start_date=contract,
                planned_end_date=deadline,
                actual_end_date=done,
                flow=flow,
                number_in_flow=index,
            )
            create_project(project)
            # Группа = команда проекта
            Group.objects.get_or_create(
                flow=flow, number=index, defaults={'project': project},
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(
            f'Поток {flow.number}: создано проектов {created}, '
            f'групп {flow.groups.count()}.',
        ))

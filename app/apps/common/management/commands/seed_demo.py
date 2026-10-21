"""Создание демонстрационных данных (ТЗ §48): python manage.py seed_demo"""
import datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.clients.models import Client
from apps.projects.models import (
    Project,
    ProjectPriority,
    ProjectStageKey,
    ProjectStatus,
    ProjectType,
)
from apps.projects.services import create_project

User = get_user_model()


class Command(BaseCommand):
    help = 'Создаёт демонстрационных клиентов, проекты и пользователей'

    def handle(self, *args, **options):
        if Project.objects.exists():
            self.stdout.write(self.style.WARNING(
                'Данные уже есть — seed_demo пропущен. '
                'Очистите БД, чтобы пересоздать демо.',
            ))
            return

        today = timezone.localdate()

        pm, _ = User.objects.get_or_create(
            username='demo-pm', defaults={
                'first_name': 'Демогуль', 'last_name': 'Примерова',
                'role': User.Role.PROJECT_MANAGER,
            },
        )
        tl, _ = User.objects.get_or_create(
            username='demo-lead', defaults={
                'first_name': 'Тестбек', 'last_name': 'Образцов',
                'role': User.Role.TEAM_LEAD,
            },
        )

        type_names = [
            'Web-сайт', 'Web-приложение', 'Mobile App', 'Landing Page',
            'CRM', 'LMS', 'Telegram Bot', 'Другое',
        ]
        types = {
            name: ProjectType.objects.get_or_create(name=name)[0]
            for name in type_names
        }

        clients_data = [
            ('ОсОО «Альфа Трейд»', 'Йотабек Вариантов', 'Бишкек'),
            ('Стоматология «Дента+»', 'Каппагуль Сценова', 'Бишкек'),
            ('Кафе «Тандыр»', 'Ламбдабек Сюжетов', 'Ош'),
            ('Фитнес-клуб Energy', 'Мюгуль Кейсова', 'Бишкек'),
            ('Логистика KG Express', 'Нюхан Мокапов', 'Каракол'),
        ]
        clients = []
        for organization, contact, city in clients_data:
            clients.append(Client.objects.create(
                organization=organization, contact_name=contact, city=city,
                phone='+996 555 000 000', email='client@example.com',
            ))

        def days(n):
            return today + datetime.timedelta(days=n)

        projects_data = [
            # name, client, type, stage, deadline, progress, priority, contract, pm, tl
            ('Сайт стоматологии', clients[1], 'Web-сайт',
             ProjectStageKey.BACKEND, days(30), 45,
             ProjectPriority.MEDIUM, days(-40), pm, tl),
            ('CRM для логистики', clients[4], 'CRM',
             ProjectStageKey.FRONTEND, days(5), 40,
             ProjectPriority.HIGH, days(-60), pm, tl),
            ('Мобильное приложение фитнес-клуба', clients[3], 'Mobile App',
             ProjectStageKey.TESTING, days(2), 70,
             ProjectPriority.CRITICAL, days(-90), pm, None),
            ('Landing кафе «Тандыр»', clients[2], 'Landing Page',
             ProjectStageKey.REWORK, days(-6), 85,
             ProjectPriority.MEDIUM, days(-35), None, tl),
            ('Интернет-магазин Альфа Трейд', clients[0], 'Web-приложение',
             ProjectStageKey.DELIVERY, days(10), 95,
             ProjectPriority.HIGH, None, pm, tl),
            ('Telegram-бот записи', clients[1], 'Telegram Bot',
             ProjectStageKey.REQUIREMENTS, days(45), 10,
             ProjectPriority.LOW, days(-5), pm, tl),
        ]
        for (name, client, type_name, stage, deadline, progress,
             priority, contract, project_pm, project_tl) in projects_data:
            project = Project(
                name=name, client=client, city=client.city,
                project_type=types[type_name],
                current_stage=stage, planned_end_date=deadline,
                progress=progress, priority=priority,
                contract_date=contract, start_date=days(-30),
                status=ProjectStatus.ACTIVE,
            )
            create_project(project)

        completed = Project(
            name='Корпоративный сайт (завершён)', client=clients[0],
            city=clients[0].city, project_type=types['Web-сайт'],
            current_stage=ProjectStageKey.COMPLETED,
            status=ProjectStatus.COMPLETED,
            planned_end_date=today.replace(day=1),
            actual_end_date=today.replace(day=2),
            progress=100, contract_date=days(-120), start_date=days(-100),
        )
        create_project(completed)

        # --- Стажёры, направления, группы (ТЗ §12, §13) ---
        from apps.interns.models import Intern, InternEvaluation, InternStatus
        from apps.interns.services import add_evaluation
        from apps.tasks.models import Task, TaskPriority
        from apps.teams.models import TeamMember, TeamRole
        from apps.training.models import Specialization, TrainingGroup

        spec_names = ['Backend', 'Frontend', 'UX/UI', 'Mobile', 'Testing/QA', 'PM']
        specs = {
            name: Specialization.objects.get_or_create(name=name)[0]
            for name in spec_names
        }
        group_backend = TrainingGroup.objects.create(
            number='BE-24', specialization=specs['Backend'], branch='Бишкек',
            start_date=days(-180), end_date=days(30),
            students_count=15, expected_interns=10,
        )
        TrainingGroup.objects.create(
            number='FE-25', specialization=specs['Frontend'], branch='Бишкек',
            start_date=days(-120), end_date=days(60),
            students_count=18, expected_interns=12,
        )

        interns_data = [
            ('Альфабек Макетов', 'Backend', InternStatus.ACTIVE, group_backend),
            ('Бетагуль Шаблонова', 'Frontend', InternStatus.ACTIVE, None),
            ('Гаммахан Прототипов', 'Backend', InternStatus.READY, group_backend),
            ('Дельтагуль Эскизова', 'UX/UI', InternStatus.ACTIVE, None),
            ('Эпсилонбек Пробаев', 'Testing/QA', InternStatus.EMPLOYABLE, None),
            ('Зетагуль Черновая', 'Mobile', InternStatus.WAITING, None),
        ]
        interns = []
        for full_name, spec_name, status, group in interns_data:
            interns.append(Intern.objects.create(
                full_name=full_name, specialization=specs[spec_name],
                status=status, training_group=group, city='Бишкек',
                team_lead=tl, internship_start_date=days(-45),
            ))

        add_evaluation(InternEvaluation(
            intern=interns[0], evaluator=tl, hard_skills=4, quality=4,
            speed=3, responsibility=5, communication=4, teamwork=5,
            independence=3, comment='Хорошо растёт, надо подтянуть скорость.',
        ))
        add_evaluation(InternEvaluation(
            intern=interns[3], evaluator=tl, hard_skills=5, quality=5,
            speed=4, responsibility=5, communication=4, teamwork=4,
            independence=4,
        ))

        # --- Команды (ТЗ §11): включая перегруз для демонстрации warning ---
        # ПМ и тимлид привязаны через карточку человека (intern.user), как в
        # рабочих данных: именно так их проекты находят порталы /pm/ и /lead/.
        pm_card = Intern.objects.create(full_name=pm.get_full_name(), user=pm, status=InternStatus.ACTIVE)
        tl_card = Intern.objects.create(full_name=tl.get_full_name(), user=tl, status=InternStatus.ACTIVE)
        first_projects = list(Project.objects.order_by('id')[:4])
        TeamMember.objects.create(
            project=first_projects[0], intern=pm_card,
            role=TeamRole.PROJECT_MANAGER, workload=40, joined_at=days(-30),
        )
        TeamMember.objects.create(
            project=first_projects[0], intern=tl_card,
            role=TeamRole.TEAM_LEAD, workload=60, joined_at=days(-30),
        )
        TeamMember.objects.create(
            project=first_projects[1], intern=tl_card,
            role=TeamRole.TEAM_LEAD, workload=50, joined_at=days(-20),
        )
        TeamMember.objects.create(
            project=first_projects[0], intern=interns[0],
            role=TeamRole.BACKEND, workload=80, joined_at=days(-25),
        )
        # Новичок — только один проект (правила 2.0): второй проект — другой стажёр
        TeamMember.objects.create(
            project=first_projects[1], intern=interns[1],
            role=TeamRole.BACKEND, workload=40, joined_at=days(-10),
        )
        TeamMember.objects.create(
            project=first_projects[2], intern=interns[3],
            role=TeamRole.UXUI, workload=70, joined_at=days(-15),
        )

        # --- Задачи с дедлайнами и приоритетами ---
        Task.objects.create(
            title='Починить оплату на staging', project=first_projects[1],
            assignee=tl, priority=TaskPriority.CRITICAL, deadline=days(-1),
            author=pm,
        )
        Task.objects.create(
            title='Подготовить demo для клиента', project=first_projects[2],
            assignee=pm, priority=TaskPriority.HIGH, deadline=today,
        )
        Task.objects.create(
            title='Ревью дизайна главной', project=first_projects[0],
            assignee=tl, priority=TaskPriority.MEDIUM, deadline=days(3),
        )

        self.stdout.write(self.style.SUCCESS(
            f'Демо-данные созданы: {Client.objects.count()} клиентов, '
            f'{Project.objects.count()} проектов, '
            f'{Intern.objects.count()} стажёров, '
            f'{Task.objects.count()} задач.',
        ))

"""Тестовый выпускник для прогона бота — и сброс его состояния.

Бот ведёт человека по сценарию один раз: прошёл правила, вошёл, выбрал
— и больше ничего не покажет. Чтобы прогонять сценарий сколько угодно
раз, этой командой заводим отдельного человека и откатываем его в
начало, не трогая настоящих выпускников.

    manage.py bot_test_user --phone 0700112233 --apply
    manage.py bot_test_user --phone 0700112233 --reset --apply
"""
from django.core.management.base import BaseCommand, CommandError

from apps.interns.models import GraduateStatus, Intern, InternStatus
from apps.projects.models import Project, ProjectStatus
from apps.teams.models import TeamMember, TeamRole

TEST_NAME = 'Тестов Тест Тестович'
TEST_PROJECT = 'Тестовый проект (бот)'


class Command(BaseCommand):
    help = 'Завести тестового выпускника для бота или сбросить его в начало сценария.'

    def add_arguments(self, parser):
        parser.add_argument('--phone', help='Телефон для входа в бота.')
        parser.add_argument('--name', default=TEST_NAME, help='ФИО тестового человека.')
        parser.add_argument(
            '--reset', action='store_true',
            help='Откатить в начало: забыть чат, правила и сделанный выбор.',
        )
        parser.add_argument('--apply', action='store_true', help='Записать изменения.')

    def handle(self, *args, **options):
        name = options['name']
        phone = options['phone']
        person = Intern.objects.filter(full_name=name).first()

        if person is None and not phone:
            raise CommandError('Такого человека нет — укажите --phone, чтобы завести его.')
        if not options['apply']:
            action = 'сброшен в начало' if options['reset'] else 'заведён/обновлён'
            self.stdout.write(f'Будет {action}: {name}' + (f' ({phone})' if phone else ''))
            self.stdout.write('Предпросмотр. Для записи: --apply')
            return

        if person is None:
            person = Intern.objects.create(
                full_name=name, phone=phone, status=InternStatus.READY,
            )
            self.stdout.write(f'Заведён: {person.full_name}')
        elif phone and person.phone != phone:
            person.phone = phone
            person.save(update_fields=['phone', 'updated_at'])

        # Завершённый проект в истории — бот показывает его при входе.
        project, _ = Project.objects.get_or_create(
            name=TEST_PROJECT, defaults={'status': ProjectStatus.COMPLETED},
        )
        if project.status != ProjectStatus.COMPLETED:
            project.status = ProjectStatus.COMPLETED
            project.save(update_fields=['status', 'updated_at'])
        TeamMember.objects.update_or_create(
            project=project, intern=person,
            defaults={'role': TeamRole.BACKEND, 'status': TeamMember.Status.LEFT},
        )

        person.telegram_chat_id = None
        person.rules_accepted_at = None
        person.phone_lock_until = None
        person.graduate_status = GraduateStatus.PENDING
        person.in_resume_bank = False
        person.resume_bank_status = ''
        person.resume_bank_comment = ''
        person.status = InternStatus.READY
        person.save()
        # Членства в настоящих проектах тестовому человеку не нужны.
        TeamMember.objects.filter(intern=person).exclude(project=project).delete()
        self.stdout.write(self.style.SUCCESS(
            f'{person.full_name} ({person.phone}) готов к прогону: правила не читал, '
            'в аккаунт не входил, выбор не сделан.'
        ))

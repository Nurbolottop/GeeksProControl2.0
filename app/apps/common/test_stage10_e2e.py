"""Этап 10B: сквозные сценарии жизненного цикла (синтетические данные, Telegram
и видеосервис — моки). Каждый сценарий идёт через настоящие сервисы и бота."""
import datetime
import itertools
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.dashboard.graduation import is_graduate
from apps.interns import discipline, freeze, internships
from apps.interns.models import Intern, InternshipRequest, InternStatus
from apps.interns.sales import SalesEvent, record_sales_confirmation
from apps.projects import closure
from apps.projects.fixtures_closure import head, make_ready
from apps.projects.models import Project, ProjectStatus
from apps.projects.services import create_project
from apps.reports import weekly
from apps.tasks import workflow
from apps.tasks.models import TaskAssignment, TaskStatus
from apps.teams import staff
from apps.teams.models import TeamMember, TeamRole
from apps.tgbot import menus
from apps.tgbot.dispatcher import handle_update
from apps.training.models import Specialization
from apps.video import attendance
from apps.video import services as video
from apps.video.providers.simulator import SimulatorProvider

UserModel = get_user_model()
NEW, SECOND, SALES, AGENT = 8001, 8002, -100801, 8090
ENV = dict(GP2_ENVIRONMENT='stage', TELEGRAM_OUTBOUND_MODE='allowlist',
           TELEGRAM_TEST_CHAT_IDS={str(NEW), str(SECOND)}, TELEGRAM_TEST_GROUP_IDS={str(SALES)},
           SALES_TELEGRAM_CHAT_ID=str(SALES), SALES_AGENT_TELEGRAM_IDS={str(AGENT)},
           VIDEO_PROVIDER='simulator', VIDEO_SIMULATOR_SECRET='e2e-secret', SITE_URL='https://control.test')
PNG = b'\x89PNG\r\n\x1a\n' + b'1' * 64
_ids = itertools.count(700000)


def text(chat, value, user=None, **extra):
    message = {'message_id': next(_ids), 'date': 0, 'chat': {'id': chat, 'type': 'private' if chat > 0 else 'supergroup'},
               'from': {'id': user or chat, 'is_bot': False, 'first_name': 'Е', 'username': f'u{user or chat}'}, **extra}
    if value:
        message['text'] = value
    return {'update_id': next(_ids), 'message': message}


def press(chat, data):
    return {'update_id': next(_ids), 'callback_query': {
        'id': str(next(_ids)), 'data': data, 'from': {'id': chat, 'is_bot': False, 'first_name': 'Е'},
        'message': {'message_id': 1, 'date': 0, 'chat': {'id': chat, 'type': 'private'}}}}


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, value, **kwargs):
        self.sent.append((chat_id, value))
        return SimpleNamespace(message_id=len(self.sent))

    def answer_callback_query(self, *a, **k):
        return True

    def get_file(self, file_id):
        return SimpleNamespace(file_path=f'photos/{file_id}.jpg', file_size=len(PNG))

    def download_file(self, path):
        return PNG


@override_settings(**ENV)
class Lifecycle(TestCase):
    def setUp(self):
        cache.clear()
        self.head = head()
        self.backend = Specialization.objects.create(name='Backend')
        self.tg = FakeTelegram()
        for target in ('apps.tgbot.outbox._bot', 'apps.tgbot.groups._bot'):
            patcher = mock.patch(target, return_value=self.tg)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch('apps.graduate_bot.bot.bot', self.tg)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lead_user = UserModel.objects.create_user(username='e-lead', password='x', role='team_lead')
        self.lead = Intern.objects.create(full_name='Тимлид Сквозной', user=self.lead_user, specialization=self.backend,
                                          position='lead', staff_role='lead', staff_status='active')

    def send(self, update):
        with self.captureOnCommitCallbacks(execute=True):
            return handle_update(update)

    def project(self, name):
        project = create_project(Project(name=name, recruitment_open=True))
        staff.assign_lead(project, self.lead, self.head)
        return project

    def close(self, project):
        make_ready(project, self.head)
        with self.captureOnCommitCallbacks(execute=True):
            closure.close_project(project, self.head)
        project.refresh_from_db()

    def webhook(self, meeting, event_type, user_id, device):
        body, header = SimulatorProvider().event(event_type, meeting, user_id=user_id, participant_id=device)
        return self.client.post(reverse('video:webhook', args=['simulator']), body, content_type='application/json',
                                HTTP_X_JAAS_SIGNATURE=header)

    def graduate(self, name, chat=None):
        """Выпускник по-настоящему: участие в проекте, который руководитель закрыл."""
        project = self.project(f'Выпускной {name}')
        person = Intern.objects.create(full_name=name, specialization=self.backend, status=InternStatus.ACTIVE,
                                       branch='Бишкек', telegram_chat_id=chat)
        TeamMember.objects.create(project=project, intern=person, role=TeamRole.BACKEND, status='active')
        self.close(project)
        person.refresh_from_db()
        self.assertTrue(is_graduate(person.pk))
        return person


class ScenarioA(Lifecycle):
    """Регистрация → продажи → проект → задача → скриншот → проверка → оценка →
    собрание → закрытие проекта → выпуск."""

    def test_new_intern_full_cycle(self):
        project = self.project('Сквозной A')
        for update in (text(NEW, '/start'), text(NEW, menus.START), text(NEW, 'Сквозной Стажёр'),
                       text(NEW, '+996 700 801 801'), text(NEW, 'Backend'), text(NEW, 'Бишкек'), press(NEW, 'reg:ok')):
            self.send(update)
        intern = Intern.objects.get(telegram_chat_id=NEW)
        self.assertEqual(self.send(press(NEW, f'choose:{project.pk}')), 'choose:denied')  # до продаж — нельзя
        sale = ('#оплата\nФИО: Сквозной Стажёр\nТелефон: 0700801801\nНаправление: Backend\nГород: Бишкек\n'
                'Оплата: E2E-1')
        self.assertEqual(self.send(text(SALES, sale, user=AGENT)), 'sales:applied')
        self.assertEqual(self.send(press(NEW, f'choose_ok:{project.pk}')), 'choose:done')
        intern.refresh_from_db()
        self.assertEqual(intern.status, InternStatus.ACTIVE)
        self.assertIsNotNone(internships.current(intern))  # период открыт при вступлении
        task = workflow.create_task(project, self.lead_user, title='Страница входа')
        with self.captureOnCommitCallbacks(execute=True):
            workflow.assign(task, intern, self.lead_user)
        self.send(press(NEW, f'start:{task.pk}'))
        self.send(press(NEW, f'submit:{task.pk}'))
        self.send(text(NEW, '', photo=[{'file_id': 'p1', 'file_size': 80, 'width': 800}]))
        self.assertEqual(self.send(text(NEW, 'Готово https://github.com/e2e/pr/1')), 'submit:done')
        submission = task.submissions.get()
        with self.captureOnCommitCallbacks(execute=True):
            workflow.review(submission, self.lead_user, approve=True)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)
        weekly.evaluate(self.lead_user, intern, project, 9, 'сильная неделя')
        meeting = video.create_meeting(self.lead_user, project, kind='required', topic='Планёрка A',
                                       direction='backend', intern_ids=[intern.pk],
                                       starts_at=timezone.now() - datetime.timedelta(minutes=10))
        participant = meeting.participants.get()
        self.webhook(meeting, 'PARTICIPANT_JOINED', meeting.host_user_id, 'h')
        self.webhook(meeting, 'PARTICIPANT_JOINED', participant.provider_user_id, 'i')
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, 'held')
        self.assertEqual(attendance.compute(meeting)[0].value, 'auto_present')
        self.close(project)
        self.assertTrue(is_graduate(intern.pk))
        self.assertEqual(TeamMember.objects.get(intern=intern, project=project).left_reason, 'project_ended')
        self.assertTrue(task.submissions.filter(outcome='approved').exists())  # история цела
        actions = set(AuditLog.objects.filter(object_type='Intern', object_id=str(intern.pk)).values_list('action',
                                                                                                         flat=True))
        self.assertTrue({'Регистрация через Telegram-бот', 'Регистрация подтверждена отделом продаж'} <= actions)
        self.assertEqual(Intern.objects.filter(phone__contains='801').count(), 1)  # один профиль


class ScenarioB(Lifecycle):
    """Выпускник → два проекта → выход из одного по заявке → второй цел → новое участие по лимитам."""

    def test_graduate_two_projects(self):
        from apps.teams.rules import check_assignment

        person = self.graduate('Выпускник Б', chat=SECOND)
        first, second, third = (self.project(f'B-{i}') for i in range(3))
        for project in (first, second):
            errors, _ = check_assignment(person, project, TeamRole.BACKEND)
            self.assertEqual(errors, [])
            TeamMember.objects.create(project=project, intern=person, role=TeamRole.BACKEND, status='active')
        self.assertTrue(check_assignment(person, third, TeamRole.BACKEND)[0])  # третий — сверх лимита
        self.assertIn(menus.LEAVE, menus.allowed_labels(person, SECOND))
        self.send(text(SECOND, '/leave'))
        self.send(press(SECOND, f'leave:{first.pk}'))
        self.send(text(SECOND, 'Не тяну два проекта'))
        self.assertEqual(self.send(press(SECOND, 'leave:ok')), 'leave:sent')
        req = InternshipRequest.objects.get(intern=person, kind='leave')
        with self.captureOnCommitCallbacks(execute=True):
            freeze.approve(req, self.head)
        self.assertEqual(TeamMember.objects.get(intern=person, project=first).status, 'left')
        self.assertEqual(TeamMember.objects.get(intern=person, project=second).status, 'active')
        self.assertTrue(is_graduate(person.pk))  # выпуск не повторяется и не теряется
        self.assertEqual(check_assignment(Intern.objects.get(pk=person.pk), third, TeamRole.BACKEND)[0], [])


class ScenarioC(Lifecycle):
    """Три предупреждения → «Оставить» → ещё три → отчисление → оплата → продажи → руководитель → новая стажировка."""

    def test_problem_internship(self):
        project = self.project('Сквозной C')
        person = Intern.objects.create(full_name='Проблемный Стажёр', phone='0700803803', specialization=self.backend,
                                       branch='Бишкек', status=InternStatus.ACTIVE)
        TeamMember.objects.create(project=project, intern=person, role=TeamRole.BACKEND, status='active')
        for _ in range(3):
            discipline.issue_warning(person, project, 'опоздал', self.lead_user)
        case = person.discipline_cases.get(status='pending')
        discipline.decide_case(case, 'keep', self.head, 'последний шанс')
        for _ in range(3):
            discipline.issue_warning(person, project, 'снова', self.lead_user)
        discipline.decide_case(person.discipline_cases.get(status='pending'), 'expel', self.head, 'три снова')
        person.refresh_from_db()
        self.assertEqual(person.status, InternStatus.EXPELLED)
        self.assertEqual(person.warnings.count(), 6)  # история предупреждений сохранена
        req = discipline.request_reenroll(person, 'оплатил снова')
        with override_settings(SALES_AGENT_TELEGRAM_IDS={str(AGENT)}):
            sale = record_sales_confirmation(SalesEvent(external_id='c-1', agent_telegram_id=str(AGENT),
                                                        phone=person.phone, full_name=person.full_name,
                                                        reference='ОПЛ-C', direction='Backend', city='Бишкек'))
        self.assertEqual(sale.status, 'applied')
        person.refresh_from_db()
        self.assertEqual(person.status, InternStatus.EXPELLED)  # продажи не зачисляют сами — нужен руководитель
        freeze.approve(InternshipRequest.objects.get(pk=req.pk), self.head)
        person.refresh_from_db()
        self.assertEqual((person.status, person.internship_attempt), (InternStatus.WAITING, 2))
        new = self.project('Сквозной C-2')
        TeamMember.objects.create(project=new, intern=person, role=TeamRole.BACKEND, status='active')
        self.assertEqual(person.internships.count(), 2)
        self.assertEqual(person.internships.filter(ended_on__isnull=True).count(), 1)  # один открытый период


class ScenarioD(Lifecycle):
    """Заявка на заморозку → одобрение → выход из проектов → задачи освобождены →
    возвращение → выбор проекта без повторной оплаты."""

    def test_freeze_and_return(self):
        project, other = self.project('Сквозной D'), self.project('Сквозной D-2')
        person = Intern.objects.create(full_name='Замороженный Стажёр', specialization=self.backend,
                                       status=InternStatus.ACTIVE, telegram_chat_id=SECOND)
        teammate = Intern.objects.create(full_name='Коллега', specialization=self.backend, status=InternStatus.ACTIVE)
        for p in (person, teammate):
            TeamMember.objects.create(project=project, intern=p, role=TeamRole.BACKEND, status='active')
        open_task = workflow.create_task(project, self.head, title='Незавершённая', direction='backend')
        done_task = workflow.create_task(project, self.head, title='Сделанная', direction='backend')
        for task in (open_task, done_task):
            workflow.assign(task, person, self.head)
        workflow.assign(open_task, teammate, self.head)
        workflow.review(workflow.submit_work(done_task.pk, person, screenshot=_png()), self.head, approve=True)
        workflow.start_work(open_task.pk, teammate)
        self.send(text(SECOND, '/freeze'))
        self.send(text(SECOND, 'Сессия в вузе'))
        self.assertEqual(self.send(press(SECOND, 'freeze:ok')), 'freeze:sent')
        with self.captureOnCommitCallbacks(execute=True):
            freeze.approve(InternshipRequest.objects.get(intern=person, kind='freeze'), self.head)
        person.refresh_from_db()
        self.assertEqual(person.status, InternStatus.PAUSED)
        self.assertFalse(TeamMember.objects.filter(intern=person, status='active').exists())
        self.assertFalse(TaskAssignment.objects.get(task=open_task, intern=person).is_active)
        self.assertEqual(TaskAssignment.objects.get(task=done_task, intern=person).status, 'done')  # результат цел
        open_task.refresh_from_db()
        self.assertEqual(open_task.status, TaskStatus.IN_PROGRESS)  # коллега работает — не «К выполнению»
        self.send(text(SECOND, '/return'))
        self.send(text(SECOND, 'Готов продолжать'))
        self.assertEqual(self.send(press(SECOND, 'return:ok')), 'return:sent')
        with self.captureOnCommitCallbacks(execute=True):
            freeze.approve(InternshipRequest.objects.get(intern=person, kind='return'), self.head)
        self.assertEqual(self.send(press(SECOND, f'choose_ok:{other.pk}')), 'choose:done')
        self.assertFalse(InternshipRequest.objects.filter(intern=person, kind='reenroll').exists())  # без оплаты
        self.assertEqual(Intern.objects.get(pk=person.pk).status, InternStatus.ACTIVE)


class ScenarioE(Lifecycle):
    """Стажёр → тимлид → проекты → резерв → трудоустройство в Studio → права отозваны."""

    def test_staff_path(self):
        from apps.reserve import employment

        user = UserModel.objects.create_user(username='e-future', password='x', role='intern')
        person = Intern.objects.create(full_name='Будущий Тимлид', user=user, specialization=self.backend,
                                       status=InternStatus.ACTIVE)
        old = self.project('Сквозной E-0')
        TeamMember.objects.create(project=old, intern=person, role=TeamRole.BACKEND, status='active')
        staff.promote_to_lead(person, self.head)
        person.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual((person.position, user.role), ('lead', 'team_lead'))
        self.assertTrue(is_graduate(person.pk))
        projects = [self.project(f'Сквозной E-{i}') for i in (1, 2)]
        for project in projects:
            staff.assign_lead(project, person, self.head)
        weekly.submit_lead_report(user, projects[0], 'отчёт тимлида')
        candidate = employment.add_to_reserve(person, self.head)
        self.assertTrue(UserModel.objects.get(pk=user.pk).is_active)  # резерв ≠ трудоустройство
        self.client.force_login(user)
        with self.captureOnCommitCallbacks(execute=True):
            employment.confirm_employment(candidate, self.head)
        self.assertFalse(UserModel.objects.get(pk=user.pk).is_active)
        self.assertFalse(TeamMember.objects.filter(intern=person, status='active').exists())
        self.assertEqual(self.client.get(reverse('lead_portal:dashboard')).status_code, 302)  # сессия мертва
        self.assertEqual(person.lead_reports.count(), 1)  # история отчётов цела


class ScenarioF(Lifecycle):
    """Бриф → проект → ПМ и тимлид → этапы → команда → 4 документа + 3 подтверждения → закрытие → выпуск."""

    def test_project_management(self):
        from apps.documents.models import ProjectBrief
        from apps.pm_portal.stages import set_stage_status

        brief = {'project_name': 'Магазин E2E', 'organization': 'ОсОО Сквозное', 'contact_name': 'Заказчик Тест',
                 'phone': '0700999000', 'email': 'e2e@example.com', 'about_business': 'Продажи',
                 'goal': 'Магазин', 'target_audience': 'Все', 'required_features': 'Каталог',
                 'references': 'нет', 'deadline_wish': 'к весне', 'existing_site_url': 'нет',
                 'domain': 'e2e.kg', 'integrations': 'нет', 'languages': 'русский', 'content_owner': 'мы',
                 'social_links': 'нет'}
        self.assertEqual(self.client.post(reverse('new_project_brief'), brief).status_code, 200)
        self.client.force_login(self.head)
        record = ProjectBrief.objects.get(project_name='Магазин E2E')
        self.client.post(reverse('documents:brief_request_accept', args=[record.pk]))
        project = Project.objects.get(name='Магазин E2E')
        Project.objects.filter(pk=project.pk).update(status=ProjectStatus.ACTIVE, recruitment_open=True)
        project.refresh_from_db()
        pm_user = UserModel.objects.create_user(username='e-pm', password='x', role='pm')
        pm = Intern.objects.create(full_name='ПМ Сквозной', user=pm_user, specialization=Specialization.objects.create(
            name='PM'), status=InternStatus.ACTIVE)
        staff.assign_pm(project, pm, self.head)
        staff.assign_lead(project, self.lead, self.head)
        for stage in project.stages.all():
            set_stage_status(project, stage, 'done', pm_user)
        member = Intern.objects.create(full_name='Участник F', specialization=self.backend, status=InternStatus.ACTIVE)
        TeamMember.objects.create(project=project, intern=member, role=TeamRole.BACKEND, status='active')
        make_ready(project, self.head)
        ready = closure.readiness(project)
        self.assertEqual((len(ready.items), ready.ready), (7, True))  # 4 документа + 3 подтверждения
        with self.captureOnCommitCallbacks(execute=True):
            closure.close_project(project, self.head)
        project.refresh_from_db()
        self.assertEqual(project.status, ProjectStatus.COMPLETED)
        self.assertTrue(is_graduate(member.pk))
        self.assertFalse(TeamMember.objects.filter(project=project, status='active', intern=member).exists())
        self.client.force_login(pm_user)
        self.assertEqual(self.client.get(reverse('pm_portal:project_detail', args=[project.pk])).status_code, 404)


def _png():
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile('r.png', PNG, content_type='image/png')


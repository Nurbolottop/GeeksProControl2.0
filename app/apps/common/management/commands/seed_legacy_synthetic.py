"""Синтетические данные со схемой GeeksPro 1.0 (этап 11A) — для репетиции переноса.

Работает через исторические модели Django на ТЕКУЩЕМ состоянии миграций базы,
поэтому применяется к базе, доведённой ровно до миграций 1.0
(`migrate <app> <последняя миграция 1.0>`). Персональных данных нет: все люди,
телефоны и заказчики выдуманы. Специально заложены неоднозначности, которые
сверка обязана найти (дубли телефона и Telegram, нет города, выход без причины,
«заморожен» с активным участием) — их нельзя исправлять автоматически.

Защита: только вне production и только в базе, в имени которой есть
«synth» или «rehearsal».
"""
import datetime

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone


class Command(BaseCommand):
    help = 'Синтетические данные схемы 1.0 для репетиции переноса (только *synth*/*rehearsal* базы).'

    def handle(self, *args, **options):
        name = connection.settings_dict['NAME']
        if settings.GP2_ENVIRONMENT == 'production' or not any(t in name for t in ('synth', 'rehearsal')):
            raise CommandError('Только вне production и только в базе *synth* / *rehearsal*.')
        loader = MigrationLoader(connection)
        self.apps = loader.project_state(list(loader.applied_migrations)).apps
        self.today = timezone.localdate()
        self.make_all()

    def model(self, label):
        return self.apps.get_model(label)

    def make(self, label, **values):
        """Создать запись исторической модели: лишние поля отбрасываются, обязательные
        без значения заполняются синтетикой по типу поля."""
        model = self.model(label)
        fields = {f.name: f for f in model._meta.concrete_fields}
        data = {k: v for k, v in values.items() if k in fields or k.endswith('_id')}
        for field in fields.values():
            if field.primary_key or field.name in data or field.has_default() or field.null or field.blank:
                continue
            if isinstance(field, models.ForeignKey):
                continue
            if isinstance(field, (models.DateTimeField,)):
                data[field.name] = timezone.now()
            elif isinstance(field, models.DateField):
                data[field.name] = self.today
            elif isinstance(field, (models.IntegerField, models.DecimalField, models.FloatField)):
                data[field.name] = 0
            elif isinstance(field, models.BooleanField):
                data[field.name] = False
            elif field.choices:
                data[field.name] = field.choices[0][0]
            else:
                data[field.name] = f'synth-{field.name}'[: getattr(field, 'max_length', None) or 50]
        return model.objects.create(**data)

    def make_all(self):
        users = {}
        for username, role in (('synth-head', 'head'), ('synth-pm', 'pm'), ('synth-lead', 'team_lead'),
                               ('synth-intern', 'intern')):
            users[role] = self.make('accounts.User', username=username, role=role, password='!unusable',
                                    is_active=True, is_superuser=False, is_staff=role == 'head')
        spec = {n: self.make('training.Specialization', name=n) for n in ('Backend', 'Frontend', 'PM')}
        client = self.make('clients.Client', organization='ОсОО Синтетика', contact_name='Заказчик Выдуманный')
        active = self.make('projects.Project', name='Синт: идущий', code='SYN-1', status='active', client=client,
                           recruitment_open=True)
        done = self.make('projects.Project', name='Синт: завершённый', code='SYN-2', status='completed',
                         client=client, actual_end_date=self.today - datetime.timedelta(days=30))
        self.make('projects.Project', name='Синт: отменённый', code='SYN-3', status='cancelled', client=client)
        people = [
            # (ФИО, телефон, branch, направление, статус, telegram)
            ('Синт Обычный', '0700000001', 'Бишкек', 'Backend', 'active', None),
            ('Синт Без Города', '0700000002', '', 'Backend', 'active', None),
            ('Синт Дубль Один', '0700000003', 'Ош', 'Frontend', 'active', 9100001),
            ('Синт Дубль Два', '+996 700 000 003', 'Ош', 'Frontend', 'waiting', 9100001),
            ('Синт Выпускник', '0700000005', 'Бишкек', 'Backend', 'active', 9100005),
            ('Синт Замороженный', '0700000006', 'Бишкек', 'Backend', 'paused', None),
            ('Синт Отчисленный', '0700000007', 'Бишкек', 'Frontend', 'expelled', None),
            ('Синт ПМ Стажёр', '0700000008', 'Бишкек', 'PM', 'active', None),
        ]
        interns = {}
        for full_name, phone, branch, direction, status, tg in people:
            interns[full_name] = self.make('interns.Intern', full_name=full_name, phone=phone, branch=branch,
                                           city=branch, specialization=spec[direction], status=status,
                                           telegram_chat_id=tg)
        interns['Синт Обычный'].user = users['intern']
        interns['Синт Обычный'].save()
        pm_card = self.make('interns.Intern', full_name='Синт ПМ Сотрудник', user=users['pm'], status='active',
                            specialization=spec['PM'])
        lead_card = self.make('interns.Intern', full_name='Синт Тимлид', user=users['team_lead'], status='active',
                              specialization=spec['Backend'], position='lead')
        member = lambda project, intern, role, **kw: self.make('teams.TeamMember', project=project,  # noqa: E731
                                                               intern=intern, role=role, **kw)
        member(active, interns['Синт Обычный'], 'backend', status='active')
        member(active, interns['Синт Без Города'], 'backend', status='active')
        member(active, interns['Синт Замороженный'], 'backend', status='active')  # противоречие для сверки
        member(active, interns['Синт ПМ Стажёр'], 'pm', status='active')
        member(active, pm_card, 'pm', status='active')
        member(active, lead_card, 'team_lead', status='active')
        member(done, interns['Синт Выпускник'], 'backend', status='left', left_reason='project_ended',
               left_at=done.actual_end_date)
        member(done, interns['Синт Дубль Один'], 'frontend', status='left', left_reason='')  # без причины
        for title, status, who in (('Синт: открытая', 'in_progress', users['intern']), ('Синт: сделанная', 'done',
                                                                                         users['intern']),
                                   ('Синт: без исполнителя', 'new', None)):
            self.make('tasks.Task', project=active, title=title, status=status, assignee=who, author=users['head'])
        doc_type = self.make('documents.DocumentType', name='Договор (синтетика)')
        document = self.model('documents.Document')(project=active, doc_type=doc_type)
        document.file.save('synth-contract.pdf', ContentFile(b'%PDF-1.4 synthetic contract'), save=False)
        for field in document._meta.concrete_fields:
            if not field.null and not field.has_default() and not field.blank and not field.primary_key \
                    and getattr(document, field.attname, None) in (None, ''):
                setattr(document, field.attname, self.today if isinstance(field, models.DateField) else 'synth')
        document.save()
        week = self.today - datetime.timedelta(days=self.today.weekday() + 7)
        self.make('reports.WeeklyReport', week_start=week, data={'interns': {'active': 5}, 'projects': {'active': 1}})
        self.make('interns.InternEvaluation', intern=interns['Синт Обычный'], project=active, evaluator=users['team_lead'],
                  hard_skills=4, quality=4, speed=3, responsibility=5, communication=4, teamwork=4, independence=3)
        self.make('audit.AuditLog', object_type='Intern', object_id=str(interns['Синт Выпускник'].pk),
                  object_repr='Синт Выпускник', action='Статус изменён', user=users['head'])
        counts = {label: self.model(label).objects.count() for label in (
            'accounts.User', 'interns.Intern', 'projects.Project', 'teams.TeamMember', 'tasks.Task',
            'documents.Document')}
        self.stdout.write(f'Синтетика 1.0 создана: {counts}')

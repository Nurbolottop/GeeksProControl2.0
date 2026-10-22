"""Этап 11D: объединение данных 1.0 с правилами 2.0 — кадровые поля и филиал."""
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from apps.audit.models import AuditLog
from apps.interns.models import Intern, Position, StaffRole, StaffStatus
from apps.interns.services import branch_from_text
from apps.projects.models import Project
from apps.teams.models import TeamMember, TeamRole
from apps.teams.selectors import staff_pm_ids
from apps.teams.test_stage6a import member, person
from apps.training.models import Specialization


class StaffBackfillTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Идущий', status='active')
        pm_spec = Specialization.objects.create(name='PM')
        self.lead = person('lead', role='team_lead', position=Position.TEAM_LEAD)
        self.pm = person('pm', role='pm', spec=pm_spec, position=Position.PM)
        member(self.project, self.pm, TeamRole.PROJECT_MANAGER)
        self.pm_no_project = person('pm2', role='pm', spec=pm_spec, position=Position.PM)
        self.pm_as_intern = person('pm3', role='pm', spec=pm_spec, position=Position.INTERN)
        member(self.project, self.pm_as_intern, TeamRole.PROJECT_MANAGER)

    def run_cmd(self, *args):
        out = StringIO()
        call_command('staff_backfill', *args, stdout=out)
        return out.getvalue()

    def test_report_changes_nothing(self):
        text = self.run_cmd()
        self.assertIn('Однозначно сотрудники: 1', text)  # только тимлид: ПМ — стажёры
        self.assertIn('Требуют решения руководителя: 0', text)
        self.assertFalse(Intern.objects.exclude(staff_status='').exists())

    def test_apply_only_unambiguous_and_is_idempotent(self):
        self.run_cmd('--apply')
        self.lead.refresh_from_db()
        self.pm.refresh_from_db()
        self.assertEqual((self.lead.staff_status, self.lead.staff_role), (StaffStatus.ACTIVE, StaffRole.LEAD))
        self.assertIsNone(self.lead.staff_since)  # дату начала не придумываем
        for unclear in (self.pm, self.pm_no_project, self.pm_as_intern):  # ПМ — стажёры
            unclear.refresh_from_db()
            self.assertEqual(unclear.staff_status, '')
        self.assertEqual(staff_pm_ids(), set())
        self.assertEqual(AuditLog.objects.filter(action__startswith='Кадровый статус заполнен').count(), 1)
        self.assertIn('Заполнено: 0', self.run_cmd('--apply'))
        self.assertEqual(AuditLog.objects.filter(action__startswith='Кадровый статус заполнен').count(), 1)

    def test_disabled_account_is_not_backfilled(self):
        self.lead.user.is_active = False
        self.lead.user.save()
        self.run_cmd('--apply')
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.staff_status, '')
        self.assertEqual(TeamMember.objects.count(), 2)  # назначения не трогаются


class BranchFromLegacyTextTests(SimpleTestCase):
    def test_latin_and_lowercase(self):
        for value, expected in (('Osh', 'Ош'), ('osh', 'Ош'), ('ош', 'Ош'), ('Ошский филиал', 'Ош'),
                                ('Bishkek', 'Бишкек'), ('Внешние стажёры(Биш)', 'Бишкек'), ('Токмок', 'Без филиала'),
                                ('', 'Без филиала')):
            self.assertEqual(branch_from_text(value), expected, value)

    def test_branch_wins_over_city(self):
        self.assertEqual(branch_from_text('Бишкек', 'Osh'), 'Бишкек')
        self.assertEqual(branch_from_text('', 'Osh'), 'Ош')


class BranchFilterTests(TestCase):
    def test_filter_matches_branch_of(self):
        from apps.interns.services import branch_filter, branch_of

        for i, (branch, city) in enumerate((('', 'Osh'), ('', 'bishkek'), ('Ош', ''), ('', 'Токмок'))):
            Intern.objects.create(full_name=f'п{i}', branch=branch, city=city)
        for name in ('Ош', 'Бишкек', 'Без филиала'):
            via_filter = set(Intern.objects.filter(branch_filter(name)).values_list('pk', flat=True))
            via_python = {i.pk for i in Intern.objects.all() if branch_of(i) == name}
            self.assertEqual(via_filter, via_python, name)


class DuplicateMembershipTests(TestCase):
    """11F: тот же человек на то же направление проекта — второй раз не добавляется."""

    def test_same_person_same_role_blocked_even_with_closed_recruitment(self):
        from apps.teams.services import save_membership

        project = Project.objects.create(name='Без дублей', status='active', recruitment_open=False)
        intern = Intern.objects.create(full_name='Один Человек')
        TeamMember.objects.create(project=project, intern=intern, role=TeamRole.BACKEND, status='active')
        error = save_membership(TeamMember(project=project, intern=intern, role=TeamRole.BACKEND, status='active'))
        self.assertIn('уже в команде', error)
        self.assertEqual(TeamMember.objects.filter(project=project, intern=intern).count(), 1)


class AddedToProjectClosesChoiceTests(TestCase):
    """11F: подтверждённая продажами стажировка + добавление в проект руководителем → бот не просит выбрать проект."""

    def test_activate_sets_chosen_project(self):
        from apps.interns.freeze import open_return
        from apps.interns.models import InternshipRequest
        from apps.teams.services import activate_intern_membership

        project = Project.objects.create(name='Сразу в проект', status='active', recruitment_open=True)
        intern = Intern.objects.create(full_name='Новый Стажёр', status='waiting')
        InternshipRequest.objects.create(intern=intern, kind=InternshipRequest.Kind.ENROLL,
                                         status=InternshipRequest.Status.APPROVED)
        self.assertIsNotNone(open_return(intern))
        member = TeamMember.objects.create(project=project, intern=intern, role=TeamRole.BACKEND, status='active')
        activate_intern_membership(member)
        self.assertIsNone(open_return(intern))
        self.assertEqual(InternshipRequest.objects.get(intern=intern).chosen_project, project)

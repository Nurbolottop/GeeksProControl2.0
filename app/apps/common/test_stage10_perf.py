"""Этап 10B: производительность тяжёлых страниц на синтетическом объёме.
Число SQL-запросов не должно расти с числом записей (N+1) — проверяем на
двух объёмах; абсолютные пороги — защита от регрессий."""
import datetime
import os
import time

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.interns.models import Intern, InternStatus
from apps.projects.fixtures_closure import head
from apps.projects.models import Project
from apps.projects.services import create_project
from apps.reports.models import LeadWeeklyReport
from apps.tasks.models import Task, TaskAssignment
from apps.teams.models import TeamMember, TeamRole
from apps.training.models import Specialization
from apps.video.models import MeetingParticipant, VideoMeeting

UserModel = get_user_model()
PAGES = [('dashboard', {}), ('projects:list', {}), ('interns:list', {}), ('interns:graduates', {}),
         ('reserve:board', {}), ('weekly_reports:board', {}), ('video:list', {}), ('teams:lead_list', {}), ('attendance:dashboard', {})]


def build(scale):
    backend = Specialization.objects.get_or_create(name='Backend')[0]
    lead_user = UserModel.objects.create_user(username=f'perf-lead-{scale}', password='x', role='team_lead')
    lead = Intern.objects.create(full_name=f'Тимлид {scale}', user=lead_user, specialization=backend, position='lead',
                                 staff_role='lead', staff_status='active')
    projects = [create_project(Project(name=f'П{scale}-{i}', recruitment_open=True)) for i in range(scale // 10)]
    interns = Intern.objects.bulk_create([
        Intern(full_name=f'Стажёр {scale} {i}', specialization=backend, status=InternStatus.ACTIVE,
               phone=f'0700{scale:03d}{i:03d}') for i in range(scale)])
    members = [TeamMember(project=projects[i % len(projects)], intern=p, role=TeamRole.BACKEND, status='active')
               for i, p in enumerate(interns)]
    members += [TeamMember(project=pr, intern=lead, role=TeamRole.TEAM_LEAD, status='active') for pr in projects]
    TeamMember.objects.bulk_create(members)
    tasks = Task.objects.bulk_create([Task(project=projects[i % len(projects)], title=f'Задача {i}', direction='backend')
                                      for i in range(scale * 3)])
    TaskAssignment.objects.bulk_create([TaskAssignment(task=t, intern=interns[i % scale], person_name='x',
                                                       role='backend') for i, t in enumerate(tasks)])
    week = timezone.localdate() - datetime.timedelta(days=timezone.localdate().weekday())
    meetings = VideoMeeting.objects.bulk_create([VideoMeeting(
        project=pr, direction='backend', topic='Собрание', starts_at=timezone.now(), week_start=week, host=lead,
        provider='simulator') for pr in projects])
    MeetingParticipant.objects.bulk_create([MeetingParticipant(meeting=meetings[i % len(meetings)], intern=p,
                                                               person_name=p.full_name) for i, p in enumerate(interns)])
    LeadWeeklyReport.objects.bulk_create([LeadWeeklyReport(lead=lead, project=pr, direction='backend', week_start=week,
                                                           text='отчёт') for pr in projects])
    return projects[0]


@override_settings(VIDEO_PROVIDER='simulator', VIDEO_SIMULATOR_SECRET='x')
class PerformanceTests(TestCase):
    def measure(self, url):
        with CaptureQueriesContext(connection) as ctx:
            started = time.perf_counter()
            response = self.client.get(url)
            elapsed = (time.perf_counter() - started) * 1000
        self.assertEqual(response.status_code, 200, url)
        return len(ctx), elapsed

    def test_query_counts_do_not_grow_with_data(self):
        user = head()
        self.client.force_login(user)
        small_project = build(40)
        small = {name: self.measure(reverse(name, kwargs=kw)) for name, kw in PAGES}
        small['kanban'] = self.measure(f'{small_project.get_absolute_url()}?tab=tasks')
        current = (timezone.localdate() - datetime.timedelta(days=timezone.localdate().weekday())).isoformat()
        small['dashboard+week'] = self.measure(f"{reverse('dashboard')}?week={current}")
        big_project = build(400)
        big = {name: self.measure(reverse(name, kwargs=kw)) for name, kw in PAGES}
        big['kanban'] = self.measure(f'{big_project.get_absolute_url()}?tab=tasks')
        big['dashboard+week'] = self.measure(f"{reverse('dashboard')}?week={current}")
        report = [f'{name:24} запросов {small[name][0]:4} → {big[name][0]:4}   время {big[name][1]:7.0f} мс'
                  for name in big]
        if os.environ.get('GP2_PERF_REPORT'):
            print('\n' + '\n'.join(report))
        for name in big:
            with self.subTest(page=name):
                # N+1: при росте данных в 10 раз запросов почти столько же
                self.assertLessEqual(big[name][0], small[name][0] + 15, report)


@override_settings(VIDEO_PROVIDER='simulator', VIDEO_SIMULATOR_SECRET='x')
class MeetingSummaryPerfTests(TestCase):
    def count(self):
        from apps.dashboard.metrics import week_of
        from apps.video.obligations import summary

        with CaptureQueriesContext(connection) as ctx:
            summary(week_of(timezone.localdate()))
        return len(ctx)

    def test_meeting_summary_does_not_grow(self):
        build(40)
        small = self.count()
        build(400)
        big = self.count()
        if os.environ.get('GP2_PERF_REPORT'):
            print(f'\nсводка собраний: запросов {small} → {big}')
        self.assertLessEqual(big, small + 15)

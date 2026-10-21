"""Этап 9B: события видеосервиса, посещаемость, исправления, обязательства,
уведомления, Dashboard. Видеосервис — симулятор с подписью формата JaaS."""
import datetime
import time

from django.core.exceptions import PermissionDenied
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.dashboard import metrics
from apps.notifications.models import Notification
from apps.teams import staff
from apps.teams.models import TeamMember, TeamRole
from apps.tgbot.models import OutboundMessage
from apps.video import attendance, checks, obligations
from apps.video.models import AttendanceCorrection, ProviderEvent, VideoMeeting
from apps.video.providers.simulator import SimulatorProvider
from apps.video.test_stage9a import SIM, Base

SIM_PROVIDER = SimulatorProvider


def last_week_moment(day=1, hour=10):
    today = timezone.localdate()
    monday = today - datetime.timedelta(days=today.weekday() + 7)
    return timezone.make_aware(datetime.datetime.combine(monday + datetime.timedelta(days=day), datetime.time(hour)))


@override_settings(**SIM)
class EventBase(Base):
    def post(self, event_type, meeting, *, user_id='', name='', device='', key=None, ts=None, secret=None, reason='',
             signed_at=None):
        provider = SIM_PROVIDER()
        body, header = provider.event(event_type, meeting, user_id=user_id, name=name, participant_id=device,
                                      key=key, ts=ts, reason=reason, signed_at=signed_at)
        if secret:
            header = provider.sign(secret, body, int(time.time()))
        return self.client.post(reverse('video:webhook', args=['simulator']), body, content_type='application/json',
                                HTTP_X_JAAS_SIGNATURE=header)

    def held_meeting(self, **kw):
        meeting = self.meeting(**kw)
        a = meeting.participants.get(intern=self.a)
        self.post('ROOM_CREATED', meeting)
        self.post('PARTICIPANT_JOINED', meeting, user_id=meeting.host_user_id, device='h1')
        self.post('PARTICIPANT_JOINED', meeting, user_id=a.provider_user_id, device='a1', name='Стажёр А')
        return meeting


class WebhookTests(EventBase):
    def test_signature_replay_and_unknown(self):
        meeting = self.meeting()
        self.assertEqual(self.post('ROOM_CREATED', meeting, secret='wrong').status_code, 401)
        self.assertEqual(self.post('ROOM_CREATED', meeting, signed_at=time.time() - 3600).status_code, 401)  # устарело
        response = self.client.post(reverse('video:webhook', args=['simulator']), b'{}',
                                    content_type='application/json')
        self.assertEqual(response.status_code, 401)  # без подписи
        self.assertEqual(self.client.post(reverse('video:webhook', args=['jaas']), b'{}',
                                          content_type='application/json').status_code, 404)
        self.assertEqual(self.post('ROOM_CREATED', meeting, key='k1').json()['result'], 'room')
        self.assertEqual(self.post('ROOM_CREATED', meeting, key='k1').json()['result'], 'duplicate')
        other = VideoMeeting(room='gp2-foreign', provider='simulator', starts_at=meeting.starts_at)
        self.assertEqual(self.post('PARTICIPANT_JOINED', other, user_id='x').json()['result'], 'unknown_room')
        self.assertEqual(ProviderEvent.objects.count(), 2)

    def test_held_only_with_host_and_verified_participant(self):
        meeting = self.meeting()
        self.post('PARTICIPANT_JOINED', meeting, user_id=meeting.host_user_id, device='h')
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, 'planned')  # ведущий один — не собрание
        self.post('PARTICIPANT_JOINED', meeting, user_id='', name='Стажёр А', device='anon')  # без ссылки
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, 'planned')  # имя ничего не подтверждает
        a = meeting.participants.get(intern=self.a)
        other_meeting = self.meeting(starts_at=meeting.starts_at + datetime.timedelta(hours=3))
        foreign = other_meeting.participants.get(intern=self.b)
        self.assertEqual(self.post('PARTICIPANT_JOINED', meeting, user_id=foreign.provider_user_id).json()['result'],
                         'unverified')  # userId другой встречи
        self.post('PARTICIPANT_JOINED', meeting, user_id=a.provider_user_id, device='a1')
        meeting.refresh_from_db()
        self.assertEqual((meeting.status, meeting.held_source), ('held', 'provider'))
        self.assertEqual(attendance.identity_issues(meeting)['unverified'], 2)


class AttendanceTests(EventBase):
    def test_reconnect_two_devices_and_durations(self):
        meeting = self.meeting()
        a = meeting.participants.get(intern=self.a)
        t0 = int(time.time())
        self.post('PARTICIPANT_JOINED', meeting, user_id=a.provider_user_id, device='phone', ts=t0)
        self.post('PARTICIPANT_LEFT', meeting, user_id=a.provider_user_id, device='phone', ts=t0 + 600, reason='left')
        self.post('PARTICIPANT_JOINED', meeting, user_id=a.provider_user_id, device='phone', ts=t0 + 900)
        self.post('PARTICIPANT_JOINED', meeting, user_id=a.provider_user_id, device='laptop', ts=t0 + 960)
        self.post('PARTICIPANT_LEFT', meeting, user_id=a.provider_user_id, device='laptop', ts=t0 + 1500)
        self.post('PARTICIPANT_LEFT', meeting, user_id=a.provider_user_id, device='phone', ts=t0 + 1800)
        row = next(r for r in attendance.compute(meeting) if r.participant == a)
        self.assertEqual((row.value, row.joins, row.devices), ('auto_present', 3, 2))
        self.assertEqual(row.minutes, 25)  # 10 + 15 (пересечение устройств не удваивает)
        self.assertTrue(row.parallel)
        summary = obligations.summary(metrics.week_of(timezone.localdate()))
        self.assertEqual(summary['unique_participants'], 1)  # один человек, а не два

    def test_absent_no_data_and_unknown_duration(self):
        meeting = self.meeting(starts_at=timezone.now() - datetime.timedelta(hours=3))
        rows = {r.participant.intern_id: r for r in attendance.compute(meeting)}
        self.assertEqual(rows[self.a.pk].value, 'no_data')  # событий нет — нет и «отсутствовал»
        a = meeting.participants.get(intern=self.a)
        a.link_opened_at = timezone.now()
        a.save()
        self.post('PARTICIPANT_JOINED', meeting, user_id=meeting.host_user_id, device='h')
        rows = {r.participant.intern_id: r for r in attendance.compute(meeting)}
        self.assertEqual(rows[self.a.pk].value, 'auto_absent')  # открытая ссылка — не посещение
        self.post('PARTICIPANT_JOINED', meeting, user_id=a.provider_user_id, device='a')
        rows = {r.participant.intern_id: r for r in attendance.compute(meeting)}
        self.assertEqual((rows[self.a.pk].value, rows[self.a.pk].minutes), ('auto_present', None))  # нет выхода

    def test_manual_correction_keeps_events(self):
        meeting = self.held_meeting(starts_at=timezone.now() - datetime.timedelta(hours=3))
        b = meeting.participants.get(intern=self.b)
        with self.assertRaises(attendance.AttendanceError):
            attendance.correct(b, self.lead_user, 'present', '')
        for user in (self.pm_user, self.front_user):
            with self.assertRaises(PermissionDenied):
                attendance.correct(b, user, 'present', 'была по телефону')
        attendance.correct(b, self.lead_user, 'excused', 'болела, справка')
        attendance.correct(b, self.head, 'present', 'подключилась с телефона мамы — подтверждено')
        row = next(r for r in attendance.compute(meeting) if r.participant == b)
        self.assertEqual((row.auto, row.value), ('auto_absent', 'manual_present'))
        history = list(AttendanceCorrection.objects.filter(participant=b))
        self.assertEqual([(c.old_value, c.new_value) for c in history],
                         [('auto_absent', 'excused'), ('manual_excused', 'present')])
        self.assertEqual(ProviderEvent.objects.filter(meeting=meeting).count(), 3)  # события не тронуты
        self.client.force_login(self.lead_user)
        page = self.client.get(reverse('lead_portal:meeting_detail', args=[meeting.pk]))
        self.assertContains(page, 'Исправлено вручную')
        self.assertNotContains(page, 'gp-legacy')

    def test_pm_meeting_attendance_not_tracked(self):
        meeting = self.meeting(user=self.pm_user, kind=VideoMeeting.Kind.PM_LEADS, direction='',
                               intern_ids=[self.lead.pk])
        with self.assertRaises(attendance.AttendanceError):
            attendance.correct(meeting.participants.get(), self.head, 'present', 'причина')


class ObligationTests(EventBase):
    def statuses(self, week, **kw):
        return {(o.project.name, o.direction): o.status for o in obligations.week_obligations(week, **kw)}

    def test_current_week_states(self):
        week = metrics.week_of(timezone.localdate())
        key = ('Видео', 'backend')
        self.assertEqual(self.statuses(week)[key], 'not_planned')
        self.assertEqual(self.statuses(week)[('Видео', 'frontend')], 'not_planned')
        self.meeting(kind=VideoMeeting.Kind.EXTRA)
        self.assertEqual(self.statuses(week)[key], 'not_planned')  # дополнительное не в счёт
        if timezone.localtime(timezone.now() + datetime.timedelta(hours=2)).date() <= week.end:
            self.meeting()
            self.assertEqual(self.statuses(week)[key], 'planned')
        self.assertIsNone(metrics.weekly_activity(week)['meetings']['obligations_missed'])  # неделя идёт

    def test_last_week_outcomes_and_history(self):
        week = metrics.week_of(last_week_moment().date())
        TeamMember.objects.all().update(joined_at=week.start - datetime.timedelta(days=30))
        moved = self.member('Ушедший Дизайнер', role=TeamRole.UXUI)
        TeamMember.objects.filter(intern=moved).update(joined_at=week.start - datetime.timedelta(days=30),
                                                       status='left', left_at=week.end)  # был в ту неделю
        late = self.member('Пришёл Позже', role=TeamRole.QA)
        TeamMember.objects.filter(intern=late).update(joined_at=week.end + datetime.timedelta(days=1))
        self.meeting(starts_at=last_week_moment(1))  # запланировано, событий нет
        front = self.meeting(user=self.front_user, direction='frontend', intern_ids=[self.f.pk],
                             starts_at=last_week_moment(2))
        self.post('ROOM_CREATED', front)  # комната была, но собрание не состоялось
        status = self.statuses(week)
        self.assertEqual(status[('Видео', 'backend')], 'insufficient')
        self.assertEqual(status[('Видео', 'frontend')], 'missed_not_held')
        self.assertEqual(status[('Видео', 'uxui')], 'no_lead')  # по составу той недели
        self.assertNotIn(('Видео', 'qa'), status)
        self.assertNotIn(('Второй видео', 'backend'), status)  # тимлид есть, стажёров нет — собрания не нужно
        summary = obligations.summary(week)
        self.assertEqual((summary['missed'], summary['insufficient'], summary['no_lead']), (1, 1, 1))

    def test_lead_two_projects_two_obligations_and_pm_meetings_excluded(self):
        week = metrics.week_of(last_week_moment().date())
        TeamMember.objects.all().update(joined_at=week.start - datetime.timedelta(days=30))
        second = self.member('Стажёр Второго', project=self.second)
        TeamMember.objects.filter(intern=second).update(joined_at=week.start - datetime.timedelta(days=30))
        mine = self.statuses(week, lead=self.lead)
        self.assertEqual(set(mine), {('Видео', 'backend'), ('Второй видео', 'backend')})
        held = self.held_meeting(starts_at=last_week_moment(3))
        self.meeting(user=self.pm_user, kind=VideoMeeting.Kind.PM_LEADS, direction='', intern_ids=[self.lead.pk],
                     starts_at=last_week_moment(3, 12))
        self.assertEqual(self.statuses(week)[('Видео', 'backend')], 'fulfilled')
        summary = obligations.summary(week)
        self.assertEqual((summary['fulfilled'], summary['pm_meetings']), (1, 1))
        self.assertEqual(held.status, 'planned')  # объект до обновления; в БД — held
        self.assertEqual(VideoMeeting.objects.get(pk=held.pk).status, 'held')
        rows = {r['key']: r for r in metrics.compare_weeks(metrics.week_of(timezone.localdate()))['rows']}
        self.assertEqual(rows['meet_fulfilled']['previous'], 1)

    def test_lead_change_keeps_history(self):
        week = metrics.week_of(last_week_moment().date())
        TeamMember.objects.all().update(joined_at=week.start - datetime.timedelta(days=30))
        self.held_meeting(starts_at=last_week_moment(1))
        new_user, new_lead = self.make_lead('v-new', 'Новый Тимлид')
        member = TeamMember.objects.get(project=self.project, intern=self.lead, role='team_lead', status='active')
        staff.replace(member, new_lead, self.head)
        self.assertEqual(self.statuses(week)[('Видео', 'backend')], 'fulfilled')
        self.assertEqual(VideoMeeting.objects.filter(host=self.lead).count(), 1)  # автор не меняется
        with self.assertRaises(PermissionDenied):
            self.meeting()  # прежний тимлид больше не ведёт направление


class ChecksAndPagesTests(EventBase):
    def test_notifications_dedup_and_telegram_queue(self):
        today = timezone.localdate()
        wednesday = today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=2)
        self.client.force_login(self.head)
        created = checks.run(wednesday)
        self.assertGreater(created, 0)
        self.assertEqual(checks.run(wednesday), 0)  # без повторов
        self.assertTrue(Notification.objects.filter(title__startswith='Не назначено обязательное собрание').exists())
        self.assertTrue(OutboundMessage.objects.filter(kind='meeting_reminder').exists())
        self.meeting(starts_at=timezone.now() - datetime.timedelta(hours=1))
        self.post('PARTICIPANT_JOINED', VideoMeeting.objects.get(), user_id='', name='Кто-то', device='z')
        checks.run(wednesday)
        self.assertTrue(Notification.objects.filter(title__startswith='Неподтверждённые участники').exists())

    def test_pages_render(self):
        meeting = self.held_meeting()
        self.client.force_login(self.head)
        page = self.client.get(reverse('video:detail', args=[meeting.pk]))
        for text in ('Посещаемость', 'Подтверждено автоматически', 'События видеосервиса'):
            self.assertContains(page, text)
        self.assertNotContains(page, meeting.participants.first().join_token)
        self.assertContains(self.client.get(reverse('video:list')), 'Обязательные собрания недели')
        self.assertContains(self.client.get(reverse('dashboard')), 'Уникальных участников')
        journal = self.client.get(reverse('tgbot:reconcile'))
        self.assertNotContains(journal, meeting.participants.first().join_token)  # ссылки в журнале скрыты
        self.client.force_login(self.pm_user)
        page = self.client.get(reverse('pm_portal:meeting_detail', args=[meeting.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'Исправить')  # ПМ — только просмотр

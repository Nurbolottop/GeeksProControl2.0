"""Ежедневные проверки собраний (этап 9B): внутренние уведомления руководителю
(Control) и напоминания тимлидам через очередь Telegram (в stage не уходят).
Без дублей — dedup_key на неделю и направление."""
from __future__ import annotations

import datetime

from django.urls import reverse
from django.utils import timezone

from apps.video.obligations import MISSED, week_obligations


def run(today=None) -> int:
    from apps.dashboard.metrics import week_of
    from apps.notifications.models import NotificationLevel
    from apps.notifications.services import notify
    from apps.tgbot import notify as telegram
    from apps.video.attendance import identity_issues
    from apps.video.models import VideoMeeting

    today = today or timezone.localdate()
    current = week_of(today, today)
    previous = week_of(current.start - datetime.timedelta(days=7), today)
    url = reverse('video:list')
    created = 0

    def head(title, key, level=NotificationLevel.WARNING, description=''):
        return bool(notify(title, level=level, description=description, url=url, dedup_key=key))

    for o in week_obligations(current, today):
        name = f'«{o.project.name}» · {o.direction_label}'
        if o.status == 'no_lead':
            created += head(f'Нет тимлида: {name}', f'meet-no-lead:{current.key}:{o.project.pk}:{o.direction}')
        elif o.status == 'not_planned' and today.weekday() >= 2:  # со среды
            created += head(f'Не назначено обязательное собрание: {name}',
                            f'meet-not-planned:{current.key}:{o.project.pk}:{o.direction}')
            for lead in o.leads:
                created += bool(telegram.to_intern(
                    lead, 'meeting_reminder', f'Назначьте обязательное собрание недели: {name}.', with_menu=False,
                    dedup_key=f'meet-remind:{current.key}:{o.project.pk}:{o.direction}:{lead.pk}'))
        elif o.status == 'awaiting':
            created += head(f'Собрание не подтверждено: {name}', f'meet-awaiting:{current.key}:{o.project.pk}:{o.direction}',
                            description='Время прошло, событий видеосервиса и ручной отметки нет.')
    for o in week_obligations(previous, today):
        name = f'«{o.project.name}» · {o.direction_label}'
        if o.status in MISSED:
            created += head(f'Пропущено обязательное собрание: {name}',
                            f'meet-missed:{previous.key}:{o.project.pk}:{o.direction}', NotificationLevel.CRITICAL, o.label)
        elif o.status == 'insufficient':
            created += head(f'Недостаточно данных о собрании: {name}',
                            f'meet-insufficient:{previous.key}:{o.project.pk}:{o.direction}',
                            description='Собрание было запланировано, но видеосервис ничего не прислал.')
    since = timezone.now() - datetime.timedelta(days=7)
    for meeting in VideoMeeting.objects.filter(starts_at__gte=since, events__verification='unverified').distinct():
        issues = identity_issues(meeting)
        if issues['unverified']:
            created += head(f'Неподтверждённые участники: {meeting.topic} ({meeting.project.name})',
                            f'meet-identity:{meeting.pk}', description=f'Подключений без персональной ссылки: '
                                                                       f'{issues["unverified"]}')
    return created

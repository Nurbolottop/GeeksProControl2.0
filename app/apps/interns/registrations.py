"""Регистрации через Telegram-бот (11F): сколько человек на каком шаге.

Шаги до конца регистрации — по журналу входящих обновлений бота (TelegramUpdate.outcome:
reg:start → reg:name → reg:phone → reg:city → reg:done), уникальные чаты за период.
Дальше — по заявкам «Новая стажировка»: подтверждение продаж, выбор проекта, вступление в группу.
"""
import datetime

from django.utils import timezone

REG_STEPS = [
    ('reg:start', 'Начали регистрацию'),
    ('reg:name', 'Ввели ФИО'),
    ('reg:phone', 'Отправили телефон'),
    ('reg:city', 'Выбрали направление и город'),
    ('reg:done', 'Завершили регистрацию'),
]
PERIODS = [('1', 'Сегодня'), ('7', '7 дней'), ('30', '30 дней'), ('all', 'Всё время')]
LAST_STEP_LABELS = dict(REG_STEPS) | {'reg:direction': 'Выбрали направление', 'reg:duplicate': 'Номер уже есть — отправлен в «Я уже стажёр»'}


def period_start(key: str):
    if key == 'all':
        return None
    today = timezone.localdate()
    days = int(key) if key.isdigit() else 7
    start = today - datetime.timedelta(days=days - 1)
    return timezone.make_aware(datetime.datetime.combine(start, datetime.time.min))


def _stage(req, joined: set):
    from apps.interns.models import InternshipRequest

    if req.status == InternshipRequest.Status.REJECTED:
        return 'Заявка отклонена', 'neutral'
    if req.sales_status != InternshipRequest.Sales.CONFIRMED:
        return 'Ждёт подтверждения продаж', 'warning'
    if not req.chosen_project_id:
        return 'Подтверждён — выбирает проект', 'info'
    if (req.intern_id, req.chosen_project_id) in joined:
        return f'В проекте «{req.chosen_project.name}», в группе', 'success'
    return f'В проекте «{req.chosen_project.name}», ссылка в группу отправлена', 'primary'


def report(period: str = '7') -> dict:
    from apps.interns.models import Intern, InternshipRequest
    from apps.tgbot.models import GroupMembership, TelegramUpdate

    since = period_start(period)
    updates = TelegramUpdate.objects.filter(outcome__startswith='reg:')
    if since:
        updates = updates.filter(received_at__gte=since)
    chats = {}
    for chat, outcome in updates.values_list('chat_id', 'outcome'):
        chats.setdefault(outcome, set()).add(chat)
    started = len(chats.get('reg:start', set()))
    steps = []
    for key, label in REG_STEPS:
        count = len(chats.get(key, set()))
        steps.append({'label': label, 'count': count, 'pct': round(count * 100 / started) if started else 0})

    reqs = (InternshipRequest.objects.filter(kind=InternshipRequest.Kind.ENROLL)
            .select_related('intern__specialization', 'chosen_project').order_by('-created_at'))
    if since:
        reqs = reqs.filter(created_at__gte=since)
    reqs = list(reqs)
    joined = set(GroupMembership.objects.filter(
        status=GroupMembership.Status.JOINED,
        member__intern_id__in=[r.intern_id for r in reqs]).values_list('member__intern_id', 'member__project_id'))
    confirmed = [r for r in reqs if r.sales_status == InternshipRequest.Sales.CONFIRMED]
    chosen = [r for r in confirmed if r.chosen_project_id]
    in_group = [r for r in chosen if (r.intern_id, r.chosen_project_id) in joined]
    after = [
        {'label': 'Подтверждены продажами', 'count': len(confirmed)},
        {'label': 'Выбрали проект', 'count': len(chosen)},
        {'label': 'Вступили в группу проекта', 'count': len(in_group)},
    ]
    for item in after:
        item['pct'] = round(item['count'] * 100 / started) if started else 0
    for r in reqs:
        r.ui_stage, r.ui_tone = _stage(r, joined)

    # начали, но не дошли до конца — профиля с этим Telegram нет
    done_chats = chats.get('reg:done', set())
    linked = set(Intern.objects.filter(telegram_chat_id__in=chats.get('reg:start', set()))
                 .values_list('telegram_chat_id', flat=True))
    stuck = []
    order = [k for k, _ in REG_STEPS] + ['reg:direction', 'reg:duplicate']
    for chat in chats.get('reg:start', set()) - done_chats - linked:
        last = (TelegramUpdate.objects.filter(chat_id=chat, outcome__startswith='reg:', outcome__in=order)
                .order_by('-received_at').first())
        stuck.append({'chat_id': chat, 'step': LAST_STEP_LABELS.get(last.outcome, last.outcome) if last else '—',
                      'at': last.received_at if last else None})
    stuck.sort(key=lambda s: s['at'] or timezone.now(), reverse=True)

    days = []
    if since:
        current = timezone.localdate(since)
        while current <= timezone.localdate():
            days.append(current)
            current += datetime.timedelta(days=1)
    daily = []
    for day in days[::-1][:31]:
        lo = timezone.make_aware(datetime.datetime.combine(day, datetime.time.min))
        hi = lo + datetime.timedelta(days=1)
        day_ups = TelegramUpdate.objects.filter(received_at__gte=lo, received_at__lt=hi)
        daily.append({
            'day': day,
            'started': day_ups.filter(outcome='reg:start').values('chat_id').distinct().count(),
            'done': day_ups.filter(outcome='reg:done').values('chat_id').distinct().count(),
            'confirmed': sum(1 for r in confirmed if r.sales_at and lo <= r.sales_at < hi),
            'chosen': sum(1 for r in chosen if r.chosen_at and lo <= r.chosen_at < hi),
        })
    return {'steps': steps, 'after': after, 'started': started, 'requests': reqs, 'stuck': stuck,
            'daily': daily, 'period': period if period in dict(PERIODS) else '7', 'periods': PERIODS}

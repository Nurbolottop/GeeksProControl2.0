"""Сценарии привязки Telegram на КОПИИ реальных данных (этап 11E) — через настоящий диспетчер.

    docker compose … run --rm --no-deps -T -e POSTGRES_DB=<копия> web python manage.py shell < scripts/bot_link_check.py

Telegram-ID вымышленные (9 000 000 001…), исходящие в режиме off остаются в журнале
«Задержано» — по нему видно меню. Только вне production. В вывод — id и кнопки, без ФИО.
"""
import itertools
import json

from django.conf import settings

from apps.interns.models import Intern
from apps.interns.sales import _digits, _name_matches, phone_candidates
from apps.teams.models import TeamMember
from apps.tgbot import menus
from apps.tgbot.dispatcher import handle_update
from apps.tgbot.models import LinkRequest, OutboundMessage

if settings.GP2_ENVIRONMENT == 'production' or settings.TELEGRAM_OUTBOUND_MODE != 'off':
    raise SystemExit('Только на копии и только с TELEGRAM_OUTBOUND_MODE=off.')
ids = itertools.count(10_000_000)
chats = itertools.count(9_000_000_001)
results = []


def check(name, ok, detail=''):
    results.append(ok)
    print(f'{"OK  " if ok else "FAIL"} {name}{" — " + str(detail) if detail and not ok else ""}')


def msg(chat, text='', contact=None):
    m = {'message_id': next(ids), 'date': 0, 'chat': {'id': chat, 'type': 'private'},
         'from': {'id': chat, 'is_bot': False, 'first_name': 'T'}}
    if text:
        m['text'] = text
    if contact:
        m['contact'] = contact
    return {'update_id': next(ids), 'message': m}


def menu(chat):
    last = OutboundMessage.objects.filter(chat_id=chat).exclude(reply_markup=None).order_by('-pk').first()
    if not last:
        return []
    data = last.reply_markup if isinstance(last.reply_markup, dict) else json.loads(last.reply_markup)
    return [b.get('text') for row in data.get('keyboard', []) for b in row]


def link(intern, chat, name=None, phone=None, owner=None):
    handle_update(msg(chat, '/start'))
    handle_update(msg(chat, menus.ALREADY))
    handle_update(msg(chat, name or intern.full_name))
    return handle_update(msg(chat, contact={'phone_number': phone or intern.phone or '0700000000',
                                            'user_id': owner or chat, 'first_name': 'T'}))


def auto_ok(i):
    if len(_digits(i.phone)) < 9:
        return False
    matched = [c for c in phone_candidates(i.phone) if not c.is_archived and _name_matches(c, i.full_name)]
    return len(matched) == 1 and matched[0].pk == i.pk


before = {'interns': Intern.objects.count(), 'members': TeamMember.objects.count()}
free = Intern.objects.filter(telegram_chat_id__isnull=True, is_archived=False).select_related('user')

# 1. Действующий стажёр в проекте — автоматическая привязка, меню стажёра
s = next(i for i in free.filter(user__isnull=True, team_memberships__status='active').distinct() if auto_ok(i))
chat = next(chats)
out = link(s, chat)
s.refresh_from_db()
check(f'стажёр id={s.pk}: автопривязка', out == 'link:linked' and s.telegram_chat_id == chat, out)
check(f'стажёр id={s.pk}: меню стажёра', menus.MY_TASKS in menu(chat) and menus.START not in menu(chat), menu(chat))
handle_update(msg(chat, '/start'))
check(f'стажёр id={s.pk}: /start повторно — без новой регистрации', menus.START not in menu(chat), menu(chat))

# 2. ПМ-сотрудник и тимлид — меню своей роли, не стажёрское
for role in ('pm', 'team_lead'):
    p = next(i for i in free.filter(user__role=role, staff_status='active') if auto_ok(i))
    chat = next(chats)
    out = link(p, chat)
    p.refresh_from_db()
    state = menus.state_of(p, chat)
    check(f'{role} id={p.pk}: привязка', out == 'link:linked' and p.telegram_chat_id == chat, out)
    check(f'{role} id={p.pk}: состояние бота {state}', state == (menus.STAFF_PM if role == 'pm' else menus.LEAD), state)
    check(f'{role} id={p.pk}: меню без «Начать стажировку»', menus.START not in menu(chat), menu(chat))

# 3. Чужой профиль: верное ФИО и телефон, но контакт не свой — отказ
victim = next(i for i in free.filter(user__isnull=True) if auto_ok(i))
out = link(victim, next(chats), owner=1)
victim.refresh_from_db()
check(f'чужой контакт для id={victim.pk}: отказ', out == 'link:refused' and victim.telegram_chat_id is None, out)

# 4. Уже привязанный профиль — с другого Telegram только ручная сверка
taken = s
chat = next(chats)
out = link(taken, chat)
taken.refresh_from_db()
check(f'профиль id={taken.pk} уже привязан: с другого Telegram — ручная сверка',
      out == 'link:manual' and taken.telegram_chat_id != chat, out)

# 5. Нет телефона — ручная сверка, кандидат по ФИО предложен руководителю
nophone = next(i for i in free.filter(phone='', user__isnull=True))
chat = next(chats)
out = link(nophone, chat, phone='0700123987')
req = LinkRequest.objects.filter(chat_id=chat).first()
check(f'без телефона id={nophone.pk}: ручная сверка, профиль в кандидатах',
      out == 'link:manual' and req is not None and nophone in req.candidates.all(), out)
nophone.refresh_from_db()
check(f'без телефона id={nophone.pk}: автоматически не привязан', nophone.telegram_chat_id is None)

# 6. Один телефон и ФИО у двух профилей — ручная сверка
dup = next(i for i in free.exclude(phone='') if not auto_ok(i) and len(_digits(i.phone)) >= 9)
chat = next(chats)
out = link(dup, chat)
dup.refresh_from_db()
check(f'дубль id={dup.pk}: ручная сверка', out == 'link:manual' and dup.telegram_chat_id is None, out)

# 7. Один Telegram не может привязать второй профиль
other = next(i for i in free.filter(user__isnull=True, telegram_chat_id__isnull=True) if auto_ok(i) and i.pk != victim.pk)
out = link(other, s.telegram_chat_id)
other.refresh_from_db()
check(f'Telegram стажёра id={s.pk} → второй профиль id={other.pk}: нельзя',
      out != 'link:linked' and other.telegram_chat_id is None, out)

after = {'interns': Intern.objects.count(), 'members': TeamMember.objects.count()}
check(f'новых профилей и участий нет ({before} → {after})', before == after)
sent = OutboundMessage.objects.filter(status='sent').count()
check('в Telegram ничего не отправлено (режим off)', sent == 0, sent)
print(f'\nПроверок: {len(results)}, ошибок: {results.count(False)}')

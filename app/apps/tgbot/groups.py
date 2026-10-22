"""Проектные и служебные Telegram-группы (этап 8B).

Привязка (группу создаёт руководитель вручную, бот её не создаёт):
1. руководитель в Control выпускает одноразовый код для проекта (или для
   группы «Все тимлиды» / «Все ПМ»), код живёт 30 минут;
2. добавляет бота в супергруппу с темами администратором с правом
   «Управление темами» и пишет в группе /bind КОД;
3. бот проверяет: код действующий и неиспользованный, чат — супергруппа с
   темами, бот — администратор с can_manage_topics, группа не привязана к
   другому проекту, у проекта нет другой группы (ограничения в БД);
4. группа появляется в Control «Ждёт подтверждения» с названием и chat_id —
   руководитель подтверждает (защита от утёкшего кода в чужой группе);
5. после подтверждения бот создаёт темы по направлениям проекта.
Вне production бот работает только с группами из TELEGRAM_TEST_GROUP_IDS
(apps.tgbot.gate + telegram_guard). Группы и темы — не граница доступа.
"""
from __future__ import annotations

import datetime
import logging
import secrets

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.tgbot import outbox
from apps.tgbot.models import BindCode, GroupMembership, GroupTopic, TelegramGroup

logger = logging.getLogger(__name__)
CODE_TTL = datetime.timedelta(minutes=30)
ANNOUNCEMENTS = 'announcements'
TOPIC_TITLES = {'backend': 'Backend', 'frontend': 'Frontend', 'uxui': 'Design', 'mobile': 'Mobile', 'qa': 'QA',
                'other': 'Другие направления', 'pm': 'PM', ANNOUNCEMENTS: 'Объявления'}
# Иконки тем — только из набора Telegram (getForumTopicIconStickers), по эмодзи.
TOPIC_ICONS = {ANNOUNCEMENTS: '📣', 'backend': '💻', 'frontend': '📺', 'uxui': '🎨', 'mobile': '📱', 'qa': '🧪',
               'pm': '💼', 'other': '💬', 'reports': '📝', 'meetings': '📆', 'payments': '💬', 'questions': '❓'}
# Темы служебных групп (11F): структура для людей; ответы продажам — в теме, где написали.
# Продажи: «Обсуждение» — туда отправляют стажёров (#оплата), ключ payments прежний.
SERVICE_TOPICS = {
    TelegramGroup.Kind.LEADS: [(ANNOUNCEMENTS, 'Объявления'), ('reports', 'Отчёты'), ('meetings', 'Собрания')],
    TelegramGroup.Kind.PMS: [(ANNOUNCEMENTS, 'Объявления'), ('reports', 'Отчёты ПМ'),
                             ('meetings', 'Встречи с тимлидами')],
    TelegramGroup.Kind.SALES: [('payments', 'Обсуждение'), ('questions', 'Вопросы')],
}


def topic_title(group, key) -> str:
    return dict(SERVICE_TOPICS.get(group.kind, [])).get(key) or TOPIC_TITLES.get(key, key)
_icon_ids: dict[str, str] = {}


def icon_id(key) -> str | None:
    """custom_emoji_id иконки темы; None — если набор недоступен (тема создастся без иконки)."""
    emoji = TOPIC_ICONS.get(key)
    if not emoji:
        return None
    if not _icon_ids:
        try:
            for sticker in _bot().get_forum_topic_icon_stickers():
                data = sticker if isinstance(sticker, dict) else sticker.__dict__
                if data.get('emoji') and data.get('custom_emoji_id'):
                    _icon_ids.setdefault(data['emoji'].replace('\ufe0f', ''), data['custom_emoji_id'])
        except Exception as error:  # сеть / Telegram — без иконки, не ломаем создание темы
            logger.info('Набор иконок тем недоступен: %s', error.__class__.__name__)
            return None
    return _icon_ids.get(emoji.replace('\ufe0f', ''))


def apply_icons(group) -> int:
    """Проставить иконки уже созданным темам (тема не пересоздаётся). Возвращает число тем."""
    from telebot.apihelper import ApiTelegramException

    done = 0
    for topic in GroupTopic.objects.filter(group=group, status=GroupTopic.Status.ACTIVE).exclude(message_thread_id=None):
        icon = icon_id(topic.key)
        if not icon:
            continue
        try:
            _bot().edit_forum_topic(group.chat_id, topic.message_thread_id, icon_custom_emoji_id=icon)
        except ApiTelegramException as error:
            if 'TOPIC_NOT_MODIFIED' not in str(getattr(error, 'description', '')):
                logger.info('Иконка темы %s не изменена: %s', topic.key, getattr(error, 'description', ''))
                continue
        done += 1
    return done


class GroupError(ValidationError):
    pass


def _bot():
    from apps.graduate_bot.bot import bot

    return bot


def _require_head(user):
    from apps.accounts.access import has_full_access

    if not has_full_access(user):
        raise PermissionDenied('Telegram-группы привязывает руководитель.')


# ============================================================ привязка

def issue_code(user, *, project=None, kind=TelegramGroup.Kind.PROJECT) -> BindCode:
    _require_head(user)
    if kind == TelegramGroup.Kind.PROJECT and project is None:
        raise GroupError('Выберите проект.')
    if kind != TelegramGroup.Kind.PROJECT:
        project = None
    live = TelegramGroup.objects.exclude(status=TelegramGroup.Status.UNBOUND)
    if (project and live.filter(project=project).exists()) or (not project and live.filter(kind=kind).exists()):
        raise GroupError('Группа уже привязана — сначала отвяжите прежнюю.')
    code = 'GP-' + secrets.token_hex(4).upper()
    return BindCode.objects.create(code=code, kind=kind, project=project, created_by=user,
                                   expires_at=timezone.now() + CODE_TTL)


def _bot_rights(chat_id):
    """(is_forum, title, can_manage_topics) — через защищённые getChat/getChatMember."""
    bot = _bot()
    chat = bot.get_chat(chat_id)
    me = bot.get_me()
    member = bot.get_chat_member(chat_id, me.id)
    can_manage = member.status == 'creator' or (member.status == 'administrator' and
                                                bool(getattr(member, 'can_manage_topics', False)))
    return bool(getattr(chat, 'is_forum', False)), getattr(chat, 'title', '') or '', can_manage


def bind(message) -> str:
    """/bind КОД в группе. Итог — для журнала обновлений и ответа в группе."""
    from apps.common.telegram_guard import TelegramBlocked

    chat = message.get('chat') or {}
    chat_id = chat.get('id')
    parts = (message.get('text') or '').split()
    code = parts[1].strip().upper() if len(parts) > 1 else ''
    if chat.get('type') != 'supergroup':
        return _say(chat_id, 'Привязка возможна только в супергруппе с темами.', 'bind:not_supergroup')
    try:
        is_forum, title, can_manage = _bot_rights(chat_id)
    except TelegramBlocked:
        return 'bind:blocked'
    except Exception as error:
        logger.info('Проверка прав бота в группе не удалась: %s', error.__class__.__name__)
        return _say(chat_id, 'Не удалось проверить права бота. Сделайте бота администратором.', 'bind:rights_unknown')
    with transaction.atomic():
        token = BindCode.objects.select_for_update().filter(code=code).first()
        if token is None or token.used_at or token.expires_at < timezone.now():
            return _say(chat_id, 'Код недействителен или устарел. Выпустите новый в Control.', 'bind:bad_code')
        if not is_forum:
            return _say(chat_id, 'Включите темы (Topics) в настройках группы и повторите.', 'bind:no_topics')
        if not can_manage:
            return _say(chat_id, 'Дайте боту право администратора «Управление темами» и повторите.', 'bind:no_rights')
        try:
            with transaction.atomic():
                group = TelegramGroup.objects.create(
                    kind=token.kind, project=token.project, chat_id=chat_id, title=title[:255] or chat.get('title', ''),
                    is_forum=is_forum, can_manage_topics=can_manage,
                    requested_by_telegram_id=(message.get('from') or {}).get('id'))
        except IntegrityError:
            return _say(chat_id, 'Эта группа уже привязана к проекту (или у проекта уже есть группа).', 'bind:conflict')
        token.used_at, token.group = timezone.now(), group
        token.save(update_fields=['used_at', 'group', 'updated_at'])
    return _say(chat_id, 'Код принят. Руководитель подтвердит привязку в Control — после этого появятся темы.',
                'bind:pending')


def _say(chat_id, text, outcome, thread_id=None):
    outbox.send('group_reply', chat_id, text, thread_id=thread_id)
    return outcome


def confirm(group: TelegramGroup, user) -> TelegramGroup:
    _require_head(user)
    with transaction.atomic():
        group = TelegramGroup.objects.select_for_update().get(pk=group.pk)
        if group.status != TelegramGroup.Status.PENDING:
            raise GroupError('Привязка уже подтверждена или отменена.')
        group.status, group.confirmed_by, group.confirmed_at = TelegramGroup.Status.ACTIVE, user, timezone.now()
        group.save(update_fields=['status', 'confirmed_by', 'confirmed_at', 'updated_at'])
    sync_topics(group)
    if group.kind == TelegramGroup.Kind.PROJECT:
        reconcile(project_ids=[group.project_id])
    return group


def unbind(group: TelegramGroup, user) -> TelegramGroup:
    """Отвязать: история (темы, подключения) остаётся, группа больше не используется."""
    _require_head(user)
    with transaction.atomic():
        group = TelegramGroup.objects.select_for_update().get(pk=group.pk)
        if group.status == TelegramGroup.Status.UNBOUND:
            return group
        group.status, group.unbound_at = TelegramGroup.Status.UNBOUND, timezone.now()
        group.save(update_fields=['status', 'unbound_at', 'updated_at'])
    return group


# =============================================================== темы

def needed_topics(group) -> list[str]:
    """Направления проекта по составу (активные участники) + объявления; у служебных — свой набор."""
    from apps.teams.models import TeamMember, TeamRole

    if getattr(group, 'kind', TelegramGroup.Kind.PROJECT) != TelegramGroup.Kind.PROJECT:
        return [key for key, _ in SERVICE_TOPICS.get(group.kind, [])]

    roles = set(TeamMember.objects.filter(project_id=group.project_id, status=TeamMember.Status.ACTIVE)
                .exclude(role=TeamRole.TEAM_LEAD).values_list('role', flat=True))
    from apps.teams.staff import lead_member_direction

    roles |= {lead_member_direction(m) for m in TeamMember.objects.filter(
        project_id=group.project_id, status=TeamMember.Status.ACTIVE, role=TeamRole.TEAM_LEAD)
        .select_related('intern__specialization')}
    keys = [k for k in TOPIC_TITLES if k in roles and k != ANNOUNCEMENTS]
    return [ANNOUNCEMENTS] + keys


def sync_topics(group) -> list[GroupTopic]:
    """Создать недостающие темы (повторно не создаёт; удалённые — заново).
    Только в привязанной группе и при праве can_manage_topics."""
    from apps.common.telegram_guard import TelegramBlocked
    from telebot.apihelper import ApiTelegramException

    group.refresh_from_db()
    if group.status != TelegramGroup.Status.ACTIVE:
        raise GroupError('Темы создаются только в привязанной группе.')
    if not group.can_manage_topics:
        raise GroupError('У бота нет права «Управление темами».')
    created = []
    for key in needed_topics(group):
        topic, _ = GroupTopic.objects.get_or_create(group=group, key=key, defaults={
            'title': topic_title(group, key), 'status': GroupTopic.Status.ERROR})
        if topic.status == GroupTopic.Status.ACTIVE and topic.message_thread_id:
            continue
        try:
            result = _bot().create_forum_topic(group.chat_id, topic.title, icon_custom_emoji_id=icon_id(key))
        except TelegramBlocked as error:
            topic.status, topic.last_error = GroupTopic.Status.ERROR, str(error)[:255]
        except ApiTelegramException as error:
            topic.status, topic.last_error = GroupTopic.Status.ERROR, str(getattr(error, 'description', ''))[:255]
        else:
            topic.message_thread_id = result.message_thread_id
            topic.status, topic.last_error = GroupTopic.Status.ACTIVE, ''
            created.append(topic)
        topic.save()
    return created


def mark_topic_missing(group, thread_id):
    """Отправка в тему вернула «тема не найдена» — пометить, при синхронизации создастся заново."""
    GroupTopic.objects.filter(group=group, message_thread_id=thread_id).update(
        status=GroupTopic.Status.MISSING, last_error='тема удалена или недоступна')


def topic_for(project, direction) -> tuple[int | None, int | None]:
    group = TelegramGroup.objects.filter(project=project, kind=TelegramGroup.Kind.PROJECT,
                                         status=TelegramGroup.Status.ACTIVE).first()
    if group is None:
        return None, None
    topic = group.topics.filter(key=direction, status=GroupTopic.Status.ACTIVE).first()
    return group.chat_id, topic.message_thread_id if topic else None


# =========================================================== участники

def reconcile(*, project_ids=None, intern_ids=None) -> int:
    """Подключения к группам по фактическому составу: новый участник —
    «нужно пригласить» (тимлиду — уведомление); закончил участие —
    «участие закончено — удалить из группы». Записи не удаляются."""
    from apps.teams.models import TeamMember, TeamRole

    groups = {g.project_id: g for g in TelegramGroup.objects.filter(kind=TelegramGroup.Kind.PROJECT,
                                                                   status=TelegramGroup.Status.ACTIVE)}
    if project_ids is not None:
        groups = {pk: g for pk, g in groups.items() if pk in set(project_ids)}
    members = TeamMember.objects.filter(project_id__in=groups, intern__isnull=False).exclude(
        role=TeamRole.TEAM_LEAD).select_related('intern', 'project')
    if intern_ids is not None:
        members = members.filter(intern_id__in=intern_ids)
    changed = 0
    for member in members:
        access = GroupMembership.objects.filter(member=member).first()
        if member.status == TeamMember.Status.ACTIVE and access is None:
            GroupMembership.objects.create(member=member, group=groups[member.project_id])
            transaction.on_commit(lambda m=member: _tell_leads_new_member(m))
            changed += 1
        elif member.status != TeamMember.Status.ACTIVE and access and access.status in (
                GroupMembership.Status.TO_INVITE, GroupMembership.Status.INVITED, GroupMembership.Status.JOINED):
            was_in = access.status == GroupMembership.Status.JOINED
            access.status = GroupMembership.Status.ACCESS_ENDED if was_in else GroupMembership.Status.REMOVED
            access.ended_at = timezone.now()
            access.save(update_fields=['status', 'ended_at', 'updated_at'])
            if was_in:
                transaction.on_commit(lambda m=member: _tell_leads(m, f'{m.intern.full_name} больше не участник '
                                                                     f'«{m.project.name}» — удалите из группы проекта.',
                                                                   f'group-ended:{m.pk}'))
            changed += 1
    return changed


def _leads_of(member):
    from apps.teams.models import TeamMember, TeamRole
    from apps.teams.staff import lead_member_direction

    leads = TeamMember.objects.filter(project_id=member.project_id, role=TeamRole.TEAM_LEAD,
                                      status=TeamMember.Status.ACTIVE).select_related('intern__specialization')
    own = [m.intern for m in leads if lead_member_direction(m) == member.role]
    return own or [m.intern for m in leads]


def _tell_leads(member, text, key):
    from apps.tgbot import notify

    for lead in _leads_of(member):
        notify.to_intern(lead, 'team_change', text, dedup_key=f'{key}:{lead.pk}', with_menu=False)


def _tell_leads_new_member(member):
    tg = member.intern.telegram or 'Telegram не указан'
    _tell_leads(member, f'Новый участник «{member.project.name}»: {member.intern.full_name} '
                        f'({member.get_role_display()}, {tg}). Пригласите в группу проекта и отметьте в Control.',
                f'group-new:{member.pk}')


def mark(access: GroupMembership, user, status) -> GroupMembership:
    """Тимлид / руководитель: «приглашение отправлено», «вступил(а)», «удалён(а)»."""
    from apps.accounts.access import has_full_access
    from apps.teams.models import TeamMember, TeamRole

    allowed = has_full_access(user) or TeamMember.objects.filter(
        project_id=access.member.project_id, intern__user=user, role=TeamRole.TEAM_LEAD,
        status=TeamMember.Status.ACTIVE).exists()
    if not allowed:
        raise PermissionDenied('Подключение отмечает тимлид проекта или руководитель.')
    flow = {GroupMembership.Status.INVITED: (GroupMembership.Status.TO_INVITE,),
            GroupMembership.Status.JOINED: (GroupMembership.Status.TO_INVITE, GroupMembership.Status.INVITED),
            GroupMembership.Status.REMOVED: (GroupMembership.Status.ACCESS_ENDED,)}
    with transaction.atomic():
        access = GroupMembership.objects.select_for_update().select_related('member').get(pk=access.pk)
        if access.status not in flow.get(status, ()):
            raise GroupError('Это действие сейчас недоступно.')
        if status == GroupMembership.Status.JOINED and access.member.status != TeamMember.Status.ACTIVE:
            raise GroupError('Участие в проекте уже закончилось.')
        now = timezone.now()
        access.status = status
        if status == GroupMembership.Status.INVITED:
            access.invited_by, access.invited_at = user, now
        elif status == GroupMembership.Status.JOINED:
            access.joined_at, access.confirmed_by, access.join_source = now, user, 'тимлид'
        else:
            access.removed_at = now
        access.save()
    return access


def on_member_update(payload) -> str:
    """chat_member: человек фактически вступил / вышел — по его Telegram id."""
    chat = payload.get('chat') or {}
    new = payload.get('new_chat_member') or {}
    user_id = (new.get('user') or {}).get('id')
    group = TelegramGroup.objects.filter(chat_id=chat.get('id'), status=TelegramGroup.Status.ACTIVE).first()
    if group is None or user_id is None:
        return 'member:unknown_group'
    access = GroupMembership.objects.filter(group=group, member__intern__telegram_chat_id=user_id).select_related(
        'member').order_by('-created_at').first()
    if access is None:
        return 'member:not_ours'
    if new.get('status') in ('member', 'administrator', 'restricted') and access.status in (
            GroupMembership.Status.TO_INVITE, GroupMembership.Status.INVITED) and access.member.status == 'active':
        access.status, access.joined_at, access.join_source = GroupMembership.Status.JOINED, timezone.now(), 'telegram'
        access.save(update_fields=['status', 'joined_at', 'join_source', 'updated_at'])
        return 'member:joined'
    if new.get('status') in ('left', 'kicked') and access.status == GroupMembership.Status.ACCESS_ENDED:
        access.status, access.removed_at = GroupMembership.Status.REMOVED, timezone.now()
        access.save(update_fields=['status', 'removed_at', 'updated_at'])
        return 'member:removed'
    return 'member:noop'


# ====================================================== вход диспетчера

def on_bot_membership(payload) -> str:
    """my_chat_member: бота добавили/удалили. Права проверяются при /bind."""
    new = (payload.get('new_chat_member') or {}).get('status')
    chat_id = (payload.get('chat') or {}).get('id')
    if new in ('left', 'kicked'):
        TelegramGroup.objects.filter(chat_id=chat_id, status=TelegramGroup.Status.ACTIVE).update(
            last_error='бота удалили из группы', can_manage_topics=False)
        return 'group:bot_removed'
    return 'group:bot_added'


def command_allowed(message) -> bool:
    """Команды в группах (/bind и др.) — только от TELEGRAM_GROUP_ADMIN_IDS, если список задан."""
    from django.conf import settings

    admins = getattr(settings, 'TELEGRAM_GROUP_ADMIN_IDS', set())
    return not admins or str((message.get('from') or {}).get('id', '')) in admins


def on_group_message(message) -> str:
    text = (message.get('text') or '').strip()
    if text.startswith('/') and not command_allowed(message):
        return 'group:cmd_denied'  # молча: посторонним бот в группе не отвечает
    if text.split('@')[0].startswith('/bind'):
        return bind(message)
    return 'group:ignored'

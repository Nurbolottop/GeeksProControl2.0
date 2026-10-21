"""Уведомления бота (этап 8): все — через очередь apps.tgbot.outbox и
GuardedTeleBot. Человеку без Telegram (не начинал диалог с ботом) сообщение
не отправить: запись остаётся в журнале со статусом «Нет Telegram».
К уведомлениям о смене статуса прикладывается обновлённое нижнее меню."""
from __future__ import annotations

from apps.tgbot import menus, outbox


def to_intern(intern, kind, text, *, dedup_key=None, with_menu=True, markup=None):
    chat = intern.telegram_chat_id
    if markup is None and with_menu and chat:
        markup = menus.menu(intern, chat)
    return outbox.send_after_commit(kind, chat, text, intern=intern, markup=markup, dedup_key=dedup_key)


def registration_confirmed(intern_id):
    from apps.interns.freeze import is_pm_direction
    from apps.interns.models import Intern

    intern = Intern.objects.filter(pk=intern_id).first()
    if intern is None:
        return None
    text = ('Оплата подтверждена отделом продаж — вы зачислены на стажировку GeeksPro! ' +
            ('Проект для направления PM назначит руководитель — напишем сюда.' if is_pm_direction(intern)
             else 'Выберите проект: кнопка «Доступные проекты».'))
    return to_intern(intern, 'registration_confirmed', text, dedup_key=f'registration-confirmed:{intern.pk}')


def project_assigned(intern, project, role_label=''):
    text = f'Вы в команде проекта «{project.name}»{f" ({role_label})" if role_label else ""}. ' \
           'Тимлид пригласит вас в Telegram-группу проекта.'
    return to_intern(intern, 'project_assigned', text, dedup_key=f'project-assigned:{intern.pk}:{project.pk}')


def _safe(func):
    """Уведомление не должно ломать бизнес-операцию."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            import logging

            logging.getLogger(__name__).exception('Уведомление %s не поставлено', func.__name__)
            return None
    wrapper.__name__ = func.__name__
    return wrapper


def _site(path):
    from django.conf import settings

    return f'{settings.SITE_URL}{path}'


@_safe
def task_assigned(assignment):
    intern = assignment.intern
    if intern is None:
        return None
    task = assignment.task
    text = f'Новая задача в «{task.project.name}»: «{task.title}»' + (
        f'\nСрок: {task.deadline:%d.%m.%Y}' if task.deadline else '')
    return to_intern(intern, 'task_assigned', text, dedup_key=f'task-assigned:{assignment.pk}',
                     markup=menus.inline([[('Открыть задачу', f'task:{task.pk}')]]))


@_safe
def submission_for_review(submission):
    from django.urls import reverse

    from apps.teams.models import TeamMember, TeamRole
    from apps.teams.staff import lead_member_direction

    task = submission.task
    direction = task.direction or submission.assignment.role
    leads = TeamMember.objects.filter(project=task.project, role=TeamRole.TEAM_LEAD,
                                      status=TeamMember.Status.ACTIVE).select_related('intern__specialization')
    url = _site(reverse('lead_portal:task_detail', args=[task.project_id, task.pk]))
    text = (f'Работа на проверке: «{task.title}» ({task.project.name}) — {submission.assignment.person_name}, '
            f'попытка {submission.attempt}.\nПроверить: {url}')
    sent = []
    for member in leads:
        if direction and lead_member_direction(member) != direction:
            continue
        sent.append(to_intern(member.intern, 'submission_review', text, with_menu=False,
                              dedup_key=f'submission-review:{submission.pk}:{member.intern_id}'))
    return sent


@_safe
def submission_reviewed(submission):
    intern = submission.assignment.intern
    if intern is None:
        return None
    task = submission.task
    if submission.outcome == 'approved':
        text = f'Работа по «{task.title}» принята. Спасибо!'
    else:
        text = f'Работа по «{task.title}» возвращена на доработку.\nЧто исправить: {submission.review_comment}'
    return to_intern(intern, 'submission_result', text, dedup_key=f'submission-result:{submission.pk}',
                     markup=menus.inline([[('Открыть задачу', f'task:{task.pk}')]]))


@_safe
def report_reminder(intern, text, week_key):
    return to_intern(intern, 'report_reminder', text, with_menu=False,
                     dedup_key=f'report-reminder:{week_key}:{intern.pk}')

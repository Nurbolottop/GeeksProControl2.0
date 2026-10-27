"""Control 2.0 → «Telegram-бот» (этап 8): ручная сверка и журналы.
Только руководитель (RoleAccessMiddleware + has_full_access)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.interns.models import Intern, InternshipRequest, SalesConfirmation
from apps.tgbot import people
from apps.tgbot.models import LinkRequest, OutboundMessage

REVIEW_SALES = (SalesConfirmation.Status.MISMATCH, SalesConfirmation.Status.AMBIGUOUS,
                SalesConfirmation.Status.UNMATCHED, SalesConfirmation.Status.NO_REQUEST)


def _head(request):
    from apps.accounts.access import has_full_access

    if not has_full_access(request.user):
        raise PermissionDenied


def _masked(messages_):
    """Ссылки в журнале не показываем: в приглашениях они персональные (секрет)."""
    import re

    items = list(messages_)
    for item in items:
        item.ui_text = re.sub(r'https?://\S+', '[ссылка]', item.text)
    return items


def _context(request):
    links = list(LinkRequest.objects.filter(status=LinkRequest.Status.PENDING).prefetch_related('candidates'))
    for item in links:
        item.ui_modal, item.ui_form = f'link-{item.pk}', f'link-form-{item.pk}'
    return {
        'links': links,
        'enrollments': InternshipRequest.objects.filter(kind=InternshipRequest.Kind.ENROLL,
                                                        status=InternshipRequest.Status.PENDING)
        .select_related('intern__specialization')[:100],
        'sales': SalesConfirmation.objects.filter(status__in=REVIEW_SALES).select_related('intern')[:50],
        'outbox': _masked(OutboundMessage.objects.select_related('intern')[:50]),
        'outbox_held': OutboundMessage.objects.filter(status=OutboundMessage.Status.HELD).count(),
        'crumbs': [{'label': 'Главная', 'href': reverse('dashboard')}, {'label': 'Telegram-бот', 'href': ''}],
    }


TABS = (('review', 'Проверка людей'), ('decisions', 'Решения по стажёрам'), ('groups', 'Группы'),
        ('members', 'Участники групп'), ('log', 'Журнал'))


@login_required
def reconcile(request):
    return reconcile_page(request)


def reconcile_page(request, tab=None):
    from apps.interns.views import decisions_context

    _head(request)
    context = _context(request)
    context.update(decisions_context())
    try:
        from apps.tgbot import group_views

        context.update(group_views.context(request))
    except ImportError:
        pass
    from apps.tgbot.models import GroupMembership

    tab = tab or (request.GET.get('tab') if request.GET.get('tab') in dict(TABS) else 'review')
    counts = {
        'review': len(context['links']) + len(context['sales']) + len(context['enrollments']),
        'decisions': context['decisions_count'],
        'groups': sum(1 for g in context.get('tg_groups', []) if g.status == 'pending'),
        'members': GroupMembership.objects.filter(status=GroupMembership.Status.TO_INVITE).count(),
    }
    base = reverse('tgbot:reconcile')
    context['tab'] = tab
    context['tg_tabs'] = [{'key': key, 'label': label, 'href': f'{base}?tab={key}', 'count': counts.get(key) or None}
                          for key, label in TABS]
    return render(request, 'tgbot/reconcile.html', context)


@login_required
@require_POST
def link_decide(request, pk):
    _head(request)
    req = get_object_or_404(LinkRequest, pk=pk)
    intern = None
    if request.POST.get('decision') == 'link':
        intern = Intern.objects.filter(pk=request.POST.get('intern') or 0).first()
        if intern is None:
            messages.error(request, 'Выберите профиль.')
            return redirect(reverse('tgbot:reconcile') + '?tab=review')
    try:
        people.decide_link(req, request.user, intern)
    except (people.PeopleError, PermissionDenied) as error:
        messages.error(request, ' '.join(getattr(error, 'messages', [str(error)])))
    else:
        messages.success(request, f'{req.full_name}: ' + ('Telegram привязан.' if intern else 'сверка отклонена.'))
    return redirect(reverse('tgbot:reconcile') + '?tab=review')

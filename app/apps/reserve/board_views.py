"""Кадровый резерв в UI 2.0 (этап 6B): кандидаты, тимлиды в резерве,
статусы рассмотрения, резюме, история, подтверждение трудоустройства.
Только руководитель (RoleAccessMiddleware); действия — POST и сервисы
apps.reserve.employment. Рекомендации компаниям, оценки и подборки
работодателям — в расширенной карточке (reserve:detail)."""
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.reserve import employment
from apps.reserve.forms import StatusChangeForm
from apps.reserve.models import CandidatePool, CandidateStatus, ReserveCandidate, StudioEmployment
from apps.training.models import Specialization

TONES = {CandidateStatus.REVIEW: 'warning', CandidateStatus.NEW: 'warning', CandidateStatus.RESERVE: 'success',
         CandidateStatus.REJECTED: 'danger', CandidateStatus.EMPLOYED: 'primary'}
KINDS = [('lead', 'Тимлиды GeeksPro'), ('intern', 'Стажёры и выпускники'), ('external', 'Внешние кандидаты')]


def _flash(request, error):
    messages.error(request, ' '.join(getattr(error, 'messages', None) or [str(error)]))


@login_required
def board(request):
    params = request.GET
    qs = ReserveCandidate.objects.active().select_related('specialization', 'intern', 'employment')
    q = (params.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(full_name__icontains=q) | Q(phone__icontains=q) | Q(telegram__icontains=q.lstrip('@'))
                       | Q(skills__icontains=q))
    if params.get('status') in CandidateStatus.values:
        qs = qs.filter(status=params['status'])
    if (params.get('specialization') or '').isdigit():
        qs = qs.filter(specialization_id=params['specialization'])
    kind = params.get('kind')
    if kind == 'lead':
        qs = qs.filter(pool=CandidatePool.STAFF, intern__isnull=False)
    elif kind == 'intern':
        qs = qs.filter(pool=CandidatePool.INTERN)
    elif kind == 'external':
        qs = qs.filter(intern__isnull=True)
    page = Paginator(qs.order_by('-updated_at', 'pk'), 25).get_page(params.get('page'))
    for c in page.object_list:
        c.ui_tone = TONES.get(c.status, 'neutral')
        c.ui_kind = ('Тимлид GeeksPro' if c.intern_id and c.pool == CandidatePool.STAFF
                     else 'Стажёр / выпускник' if c.intern_id else 'Внешний кандидат')
    return render(request, 'reserve/board.html', {
        'page': page, 'params': params, 'kinds': KINDS,
        'stat_rows': (lambda st: [('На рассмотрении', st['review']), ('В кадровом резерве', st['reserve']),
                                  ('Отклонено', st['rejected']), ('Трудоустроено в Geeks Studio', st['employed'])])(
            employment.stats()),
        'statuses': CandidateStatus.choices, 'specializations': Specialization.objects.order_by('name'),
        'crumbs': [{'label': 'Главная', 'href': reverse('dashboard')}, {'label': 'Кадровый резерв', 'href': ''}],
    })


@login_required
def hr_detail(request, pk):
    candidate = get_object_or_404(ReserveCandidate.objects.select_related('specialization', 'intern'), pk=pk)
    job = StudioEmployment.objects.filter(candidate=candidate).select_related('confirmed_by').first()
    return render(request, 'reserve/hr_detail.html', {
        'c': candidate, 'tone': TONES.get(candidate.status, 'neutral'), 'job': job,
        'job_text': (f'{job.company} · {job.employed_on:%d.%m.%Y}' + (f' · {job.position}' if job.position else '')
                     + (f' · подтвердил(а) {job.confirmed_by.display_name}' if job.confirmed_by else '')) if job else '',
        'memberships': candidate.project_memberships,
        'events': candidate.events.select_related('user')[:100],
        'status_form': StatusChangeForm(initial={'status': candidate.status}, current=candidate.status),
        'today': datetime.date.today(),
        'crumbs': [{'label': 'Главная', 'href': reverse('dashboard')},
                   {'label': 'Кадровый резерв', 'href': reverse('reserve:board')},
                   {'label': candidate.full_name, 'href': ''}],
    })


@login_required
@require_POST
def confirm_employment(request, pk):
    candidate = get_object_or_404(ReserveCandidate, pk=pk)
    raw = request.POST.get('employed_on') or ''
    try:
        day = datetime.date.fromisoformat(raw) if raw else None
    except ValueError:
        day = None
    try:
        employment.confirm_employment(candidate, request.user, day, request.POST.get('position', ''),
                                      request.POST.get('comment', ''))
    except (employment.EmploymentError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, f'{candidate.full_name}: трудоустройство в Geeks Studio подтверждено.')
    return redirect('reserve:hr_detail', pk=candidate.pk)


@login_required
@require_POST
def reserve_lead(request, intern_pk):
    from apps.interns.models import Intern

    intern = get_object_or_404(Intern, pk=intern_pk)
    try:
        candidate = employment.add_to_reserve(intern, request.user, request.POST.get('comment', ''))
    except (employment.EmploymentError, PermissionDenied) as error:
        _flash(request, error)
        return redirect('teams:staff_detail', pk=intern.pk)
    messages.success(request, f'{intern.full_name} — в кадровом резерве. Работа и права тимлида не меняются.')
    return redirect('reserve:hr_detail', pk=candidate.pk)


@login_required
@require_POST
def unreserve(request, pk):
    candidate = get_object_or_404(ReserveCandidate, pk=pk)
    try:
        employment.remove_from_reserve(candidate, request.user, request.POST.get('comment', ''))
    except (employment.EmploymentError, PermissionDenied) as error:
        _flash(request, error)
    else:
        messages.success(request, 'Убран(а) из активного резерва — история сохранена.')
    return redirect('reserve:hr_detail', pk=candidate.pk)

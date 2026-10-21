"""Резерв кадров: список, карточка кандидата, оценка, рекомендации,
приглашения на анкету и сама публичная анкета.

Внутренние страницы закрыты логином (и ролью — на изменение), публичная
анкета доступна только по токену приглашения и не даёт кандидату
ничего, кроме собственной формы.
"""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.interns.models import Intern
from apps.reserve import selectors, services
from apps.reserve.forms import (
    InviteForm, ReserveApplyForm, ReserveCandidateForm, ReserveEvaluationForm,
    ReserveRecommendationForm, ShareLinkForm, StatusChangeForm,
)
from apps.reserve.models import (
    CandidateLevel, CandidateStatus, Employment, RecommendationStatus,
    ReserveCandidate, ReserveInvite, ReserveRecommendation, ReserveShareLink,
    WorkFormat,
)
from apps.reserve.permissions import can_edit_reserve, reserve_editor_required
from apps.training.models import Specialization


@login_required
def overview(request):
    """Главная резерва: сводка по направлениям, дальше — в список."""
    rows = selectors.summary_by_specialization()
    return render(request, 'reserve/overview.html', {
        'rows': rows,
        'totals': selectors.summary_totals(rows),
        'can_edit': can_edit_reserve(request.user),
        'share_form': ShareLinkForm(),
        'share_collections': (
            selectors.share_collections({})
            if can_edit_reserve(request.user) else []
        ),
        'statuses': {
            'new': [str(s) for s in selectors.NEW_STATUSES],
            'available': [str(s) for s in selectors.AVAILABLE_STATUSES],
            'in_progress': [str(s) for s in selectors.IN_PROGRESS_STATUSES],
        },
    })


@login_required
def candidate_list(request):
    qs, sort = selectors.candidates(request.GET)
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page'))
    open_invites = [
        invite for invite in
        ReserveInvite.objects.filter(is_active=True, candidate__isnull=True)[:20]
        if invite.is_open
    ]
    context = {
        'page': page,
        'params': request.GET,
        'sort': sort,
        'sort_options': selectors.SORT_OPTIONS,
        'rating_options': selectors.RATING_OPTIONS,
        'readiness_options': selectors.READINESS_OPTIONS,
        'specializations': Specialization.objects.all(),
        'levels': CandidateLevel.choices,
        'statuses': CandidateStatus.choices,
        'work_formats': WorkFormat.choices,
        'employments': Employment.choices,
        'cities': selectors.cities(),
        'extra_filters': selectors.extra_filters_used(request.GET),
        'has_filters': selectors.any_filter_used(request.GET),
        'open_invites': open_invites,
        'invite_form': InviteForm(),
        'share_form': ShareLinkForm(),
        'share_collections': (
            selectors.share_collections(request.GET)
            if can_edit_reserve(request.user) else []
        ),
        'can_edit': can_edit_reserve(request.user),
        # перетаскивать строки есть смысл только в режиме «Свой порядок»
        'can_drag': (
            sort == selectors.DRAGGABLE_SORT and can_edit_reserve(request.user)
        ),
        'free_interns': (
            Intern.objects.active().filter(reserve_card__isnull=True)
            .order_by('full_name')
        ),
    }
    return render(request, 'reserve/list.html', context)


@login_required
def candidate_detail(request, pk):
    candidate = get_object_or_404(
        ReserveCandidate.objects.select_related(
            'specialization', 'training_group', 'intern', 'created_by', 'updated_by',
        ),
        pk=pk,
    )
    tab = request.GET.get('tab', 'general')
    if tab not in {
        'general', 'professional', 'skills', 'experience', 'projects',
        'evaluation', 'recommendations', 'history',
    }:
        tab = 'general'
    context = {
        'candidate': candidate,
        'tab': tab,
        'memberships': candidate.project_memberships,
        'recommendations': candidate.recommendations.select_related(
            'company', 'created_by',
        ),
        'events': candidate.events.select_related('user')[:100],
        'status_form': StatusChangeForm(
            initial={'status': candidate.status}, current=candidate.status,
        ),
        'edit_links': [
            link for link in candidate.edit_links.filter(is_active=True)
            if link.is_open
        ],
        'invite_form': InviteForm(),
        'share_links': [
            link for link in candidate.share_links.filter(is_active=True)
            if link.is_open
        ],
        'share_form': ShareLinkForm(),
        'rec_statuses': RecommendationStatus.choices,
        'can_edit': can_edit_reserve(request.user),
    }
    return render(request, 'reserve/detail.html', context)


@reserve_editor_required
def candidate_create(request):
    form = ReserveCandidateForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        candidate = services.create_candidate(form.save(commit=False), request.user)
        form.save_m2m()
        messages.success(request, f'{candidate.full_name} добавлен(а) в резерв.')
        return redirect(candidate.get_absolute_url())
    return render(request, 'reserve/form.html', {
        'form': form, 'title': 'Новый кандидат в резерв',
    })


@reserve_editor_required
def candidate_from_intern(request):
    """Быстро завести кандидата из карточки стажёра — без перепечатывания."""
    if request.method == 'POST':
        intern = get_object_or_404(Intern, pk=request.POST.get('intern'))
        candidate = services.candidate_from_intern(intern, request.user)
        messages.success(
            request, f'{candidate.full_name} добавлен(а) в резерв из базы стажёров.',
        )
        return redirect(candidate.get_absolute_url())
    return redirect('reserve:list')


@reserve_editor_required
def candidate_update(request, pk):
    candidate = get_object_or_404(ReserveCandidate, pk=pk)
    form = ReserveCandidateForm(
        request.POST or None, request.FILES or None, instance=candidate,
    )
    if request.method == 'POST' and form.is_valid():
        changed = ', '.join(form.changed_data)
        services.update_candidate(form.save(commit=False), request.user, detail=changed)
        messages.success(request, 'Карточка кандидата обновлена.')
        return redirect(candidate.get_absolute_url())
    return render(request, 'reserve/form.html', {
        'form': form, 'title': f'Резерв: {candidate.full_name}',
        'candidate': candidate,
    })


@reserve_editor_required
def candidate_evaluate(request, pk):
    candidate = get_object_or_404(ReserveCandidate, pk=pk)
    form = ReserveEvaluationForm(request.POST or None, instance=candidate)
    if request.method == 'POST' and form.is_valid():
        services.save_evaluation(form.save(commit=False), request.user)
        messages.success(
            request, f'Оценка сохранена, рейтинг: {candidate.rating or "—"} / 10.',
        )
        return redirect(candidate.get_absolute_url())
    return render(request, 'reserve/evaluate_form.html', {
        'form': form, 'candidate': candidate,
        'criteria': ReserveCandidate.EVALUATION_CRITERIA,
    })


@reserve_editor_required
def candidates_reorder(request):
    """Новый порядок кандидатов после перетаскивания строки.

    Приходит список id в том порядке, в каком они теперь видны на
    странице; приоритет раздаём сверху вниз. Порядок задаётся тем, что
    сотрудник видит на экране, поэтому перетаскивание доступно только
    в режиме «Свой порядок».
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    try:
        order = [int(pk) for pk in request.POST.getlist('order')]
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Некорректный порядок'}, status=400)
    if not order:
        return JsonResponse({'ok': False, 'error': 'Пустой порядок'}, status=400)
    services.reorder_candidates(order, request.user)
    return JsonResponse({'ok': True})


@reserve_editor_required
def candidate_status(request, pk):
    candidate = get_object_or_404(ReserveCandidate, pk=pk)
    # current — чтобы отправка текущего статуса воронки не считалась ошибкой
    form = StatusChangeForm(request.POST or None, current=candidate.status)
    if request.method == 'POST':
        if form.is_valid():
            try:
                services.change_status(
                    candidate, form.cleaned_data['status'],
                    comment=form.cleaned_data['comment'], user=request.user,
                )
            except ValueError as error:
                messages.error(request, str(error))
                return redirect(candidate.get_absolute_url())
            messages.success(request, f'Статус: {candidate.get_status_display()}.')
        else:
            messages.error(request, 'Статус не изменён: выберите его из списка.')
    if request.POST.get('next') == 'hr':
        return redirect('reserve:hr_detail', pk=candidate.pk)
    return redirect(candidate.get_absolute_url())


@reserve_editor_required
def invite_create(request, pk=None):
    """Ссылка на анкету: общая или на редактирование карточки кандидата."""
    candidate = get_object_or_404(ReserveCandidate, pk=pk) if pk else None
    form = InviteForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        invite = services.issue_invite(
            candidate, user=request.user, ttl_days=form.ttl(),
            recipient=form.cleaned_data['recipient'],
        )
        kind = 'на редактирование анкеты' if candidate else 'на анкету'
        messages.success(
            request,
            f'Ссылка {kind} создана: '
            f'{request.build_absolute_uri(invite.get_absolute_url())}',
        )
    if candidate is not None:
        return redirect(candidate.get_absolute_url())
    return redirect('reserve:list')


@reserve_editor_required
def invite_disable(request, pk):
    invite = get_object_or_404(ReserveInvite, pk=pk)
    if request.method == 'POST':
        invite.deactivate()
        messages.success(request, 'Ссылка на анкету отключена.')
    if invite.candidate_id:
        return redirect(invite.candidate.get_absolute_url())
    return redirect('reserve:list')


@reserve_editor_required
def share_create(request, pk):
    """Ссылка на профиль кандидата, чтобы отправить её работодателю."""
    candidate = get_object_or_404(ReserveCandidate, pk=pk)
    form = ShareLinkForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        link = services.issue_share_link(
            candidate, user=request.user, ttl_days=form.ttl(),
            recipient=form.cleaned_data['recipient'],
            show_contacts=form.cleaned_data['show_contacts'],
        )
        messages.success(
            request,
            f'Ссылка на профиль создана: '
            f'{request.build_absolute_uri(link.get_absolute_url())}',
        )
    return redirect(candidate.get_absolute_url())


@reserve_editor_required
def share_collection_create(request):
    """Одна ссылка на подборку: отмеченные галочками кандидаты, а если
    никого не отметили — все, кто попал под текущий фильтр списка."""
    filters = request.POST.get('filters', '')
    if request.POST.get('next') == 'overview':
        back = reverse('reserve:overview')
    elif filters:
        back = f"{reverse('reserve:list')}?{filters}"
    else:
        back = reverse('reserve:list')
    form = ShareLinkForm(request.POST or None)
    if request.method != 'POST' or not form.is_valid():
        return redirect(back)
    ids = [pk for pk in request.POST.getlist('candidates') if pk.isdigit()]
    if ids:
        candidates = ReserveCandidate.objects.active().filter(pk__in=ids)
    else:
        candidates, _ = selectors.candidates(QueryDict(filters))
    if not candidates.exists():
        messages.error(request, 'В подборке нет ни одного кандидата.')
        return redirect(back)
    link = services.issue_share_collection(
        candidates, user=request.user, ttl_days=form.ttl(),
        recipient=form.cleaned_data['recipient'],
        show_contacts=form.cleaned_data['show_contacts'],
    )
    messages.success(
        request,
        f'Ссылка на подборку ({link.candidates.count()} канд.) создана: '
        f'{request.build_absolute_uri(link.get_absolute_url())}',
    )
    return redirect(back)


@reserve_editor_required
def share_disable(request, pk):
    link = get_object_or_404(ReserveShareLink, pk=pk)
    if request.method == 'POST':
        link.deactivate()
        messages.success(request, 'Ссылка отключена.')
    if link.is_collection:
        if request.POST.get('next') == 'overview':
            return redirect('reserve:overview')
        return redirect('reserve:list')
    return redirect(link.candidate.get_absolute_url())


@reserve_editor_required
def recommendation_create(request, pk):
    candidate = get_object_or_404(ReserveCandidate, pk=pk)
    form = ReserveRecommendationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        recommendation = form.save(commit=False)
        recommendation.candidate = candidate
        services.add_recommendation(recommendation, request.user)
        messages.success(
            request, f'Рекомендация в «{recommendation.company_title}» сохранена.',
        )
        return redirect(candidate.get_absolute_url())
    return render(request, 'reserve/recommend_form.html', {
        'form': form, 'candidate': candidate,
    })


@reserve_editor_required
def recommendation_status(request, pk):
    recommendation = get_object_or_404(ReserveRecommendation, pk=pk)
    if request.method == 'POST':
        status = request.POST.get('status', '')
        if status in RecommendationStatus.values:
            services.update_recommendation_status(
                recommendation, status,
                comment=request.POST.get('comment', ''), user=request.user,
            )
            messages.success(request, 'Результат по рекомендации сохранён.')
    return redirect(recommendation.candidate.get_absolute_url())


# --- публичная часть: анкета кандидата по персональной ссылке ---

def apply_form(request, token):
    """Анкета кандидата. Доступна только по действующей ссылке."""
    invite = ReserveInvite.objects.filter(token=token).first()
    if invite is None or not invite.is_open:
        return render(request, 'reserve/apply_expired.html', status=404)
    form = ReserveApplyForm(
        request.POST or None, request.FILES or None, instance=invite.candidate,
    )
    if request.method == 'POST' and form.is_valid():
        services.accept_application(invite, form)
        return render(request, 'reserve/apply_done.html', {
            'is_edit': invite.is_edit_link,
        })
    return render(request, 'reserve/apply.html', {
        'form': form, 'invite': invite, 'is_edit': invite.is_edit_link,
        'direction_groups': json.dumps(services.direction_groups_map()),
        'mark_optional': True,
    })


def share_profile(request, token):
    """Витрина для работодателя — только по действующей ссылке.

    Ссылка на один профиль сразу показывает его. Ссылка на подборку
    показывает список, а профиль из неё открывается как `?c=<id>` —
    только тех кандидатов, кто в эту подборку входит.
    """
    link = ReserveShareLink.objects.filter(token=token).first()
    if link is None or not link.is_open:
        return render(request, 'reserve/share_expired.html', status=404)

    if not link.is_collection:
        candidate = ReserveCandidate.objects.active().filter(pk=link.candidate_id).first()
        if candidate is None:
            return render(request, 'reserve/share_expired.html', status=404)
        services.register_share_view(link)
        return _share_profile_page(request, link, candidate)

    candidates = (
        link.candidates.filter(is_archived=False)
        .select_related('specialization')
    )
    chosen = request.GET.get('c', '')
    if chosen:
        candidate = candidates.filter(pk=chosen).first() if chosen.isdigit() else None
        if candidate is None:
            return redirect(link.get_absolute_url())
        return _share_profile_page(
            request, link, candidate, back_url=link.get_absolute_url(),
        )
    services.register_share_view(link)
    candidates = list(candidates)
    return render(request, 'reserve/share_collection.html', {
        'link': link,
        'candidates': candidates,
        'groups': selectors.group_by_direction(candidates),
    })


def _share_profile_page(request, link, candidate, back_url=''):
    return render(request, 'reserve/share.html', {
        'link': link,
        'candidate': candidate,
        'back_url': back_url,
        'memberships': candidate.project_memberships,
        'has_scores': any(value for _, value in candidate.scores),
    })


EXTERNAL_RATE_LIMIT = 5        # заявок с одного адреса
EXTERNAL_RATE_WINDOW = 60 * 60  # за час


def _external_rate_limited(request) -> bool:
    from django.core.cache import cache

    ip = (request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR') or '').strip()
    key = f'reserve-external:{ip}'
    if cache.add(key, 1, EXTERNAL_RATE_WINDOW):
        return False
    try:
        return cache.incr(key) > EXTERNAL_RATE_LIMIT
    except ValueError:
        return False


def external_apply(request):
    """Публичная заявка внешнего кандидата в кадровый резерв (этап 6B).

    Входа в Control не даёт и учётку не создаёт. С профилем стажёра
    автоматически не связывается. Защита: ловушка для ботов, лимит заявок
    с адреса, одна заявка на браузер, повтор с тем же телефоном —
    дописывается в историю существующей заявки, а не плодит новую.
    """
    from apps.reserve.forms import ExternalCandidateForm

    if request.session.get('reserve_external_submitted'):
        return render(request, 'reserve/external_done.html')
    form = ExternalCandidateForm(request.POST or None, request.FILES or None)
    if request.method == 'POST':
        if request.POST.get('website'):
            return render(request, 'reserve/external_done.html')
        if _external_rate_limited(request):
            return render(request, 'reserve/external_done.html', {'limited': True}, status=429)
        if form.is_valid():
            services.accept_external(form)
            request.session['reserve_external_submitted'] = True
            return render(request, 'reserve/external_done.html')
    return render(request, 'reserve/external_apply.html', {'form': form})

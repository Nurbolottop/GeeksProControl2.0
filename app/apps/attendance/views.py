import datetime

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.attendance import overview, services
from apps.attendance.models import (
    Attendance, GroupMeeting, MeetingKind, WorkScore,
)
from apps.flows.models import Group
from apps.interns.models import Intern


class MeetingCreateForm(forms.Form):
    """Создание собрания: вид, дата и кто проводит."""

    kind = forms.ChoiceField(label='Вид собрания', choices=MeetingKind.choices)
    host = forms.ModelChoiceField(
        label='Какой тимлид проводит', queryset=Intern.objects.none(),
        required=False, empty_label='Любой / не указан',
    )
    topic = forms.CharField(
        label='Тема (необязательно)', required=False, max_length=255,
        widget=forms.TextInput(attrs={'placeholder': 'Например: недельный статус'}),
    )

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        if group is not None:
            # В списке — только тимлиды этой группы
            self.fields['host'].queryset = Intern.objects.filter(
                team_memberships__group=group,
                team_memberships__role='team_lead',
                team_memberships__status='active',
            ).distinct().order_by('full_name')


def _period(request) -> tuple[int, int]:
    today = timezone.localdate()
    try:
        year = int(request.GET.get('year', today.year))
        month = int(request.GET.get('month', today.month))
        datetime.date(year, month, 1)
    except (TypeError, ValueError):
        raise Http404('Некорректный период')
    return year, month


@login_required
def group_sheet(request, pk):
    """Табель группы: посещаемость собраний за месяц."""
    group = get_object_or_404(
        Group.objects.select_related('flow', 'project'), pk=pk,
    )
    year, month = _period(request)
    sheet = services.build_sheet(group, year, month)

    current = datetime.date(year, month, 1)
    prev_month = (current - datetime.timedelta(days=1)).replace(day=1)
    next_month = (current + datetime.timedelta(days=32)).replace(day=1)

    return render(request, 'attendance/group_sheet.html', {
        'group': group,
        'sheet': sheet,
        'year': year,
        'month': month,
        'current': current,
        'prev_month': prev_month,
        'next_month': next_month,
        'today': timezone.localdate(),
    })


@login_required
def meeting_create(request, pk):
    """Создать одно собрание группы на выбранную дату."""
    group = get_object_or_404(Group.objects.select_related('flow'), pk=pk)
    year, month = _period(request)
    form = MeetingCreateForm(request.POST or None, group=group)

    date_error = ''
    if request.method == 'POST' and form.is_valid():
        dates = []
        for raw in request.POST.getlist('date'):
            raw = raw.strip()
            if not raw:
                continue
            try:
                dates.append(datetime.date.fromisoformat(raw))
            except ValueError:
                continue
        if not dates:
            date_error = 'Укажите хотя бы одну дату собрания.'
        else:
            created = [
                services.create_meeting(
                    group,
                    kind=form.cleaned_data['kind'],
                    date=date,
                    host=form.cleaned_data.get('host'),
                    topic=form.cleaned_data.get('topic', ''),
                )
                for date in sorted(set(dates))
            ]
            new_count = len([item for item in created if item])
            if new_count:
                messages.success(request, f'Добавлено собраний: {new_count}.')
            else:
                messages.info(request, 'Такие собрания уже есть в табеле.')
            first = sorted(set(dates))[0]
            return redirect(
                f'/attendance/groups/{group.pk}/'
                f'?year={first.year}&month={first.month}',
            )

    return render(request, 'attendance/meeting_form.html', {
        'group': group,
        'form': form,
        'year': year,
        'month': month,
        'current': datetime.date(year, month, 1),
        'today': timezone.localdate(),
        'date_error': date_error,
    })


@login_required
def meeting_delete(request, pk):
    """Удалить собрание вместе с его отметками."""
    meeting = get_object_or_404(
        GroupMeeting.objects.select_related('group'), pk=pk,
    )
    group, date = meeting.group, meeting.date
    if request.method == 'POST':
        meeting.delete()
        messages.success(request, 'Собрание удалено.')
    return redirect('attendance:group_meetings', pk=group.pk)


@login_required
def toggle(request, pk):
    """AJAX: клик по клетке табеля переключает отметку."""
    meeting = get_object_or_404(
        GroupMeeting.objects.select_related('group'), pk=pk,
    )
    if request.method != 'POST':
        raise Http404
    intern = get_object_or_404(Intern, pk=request.POST.get('intern'))
    if not services.attendance_eligible_members(meeting.group).filter(
        intern=intern,
    ).exists():
        raise Http404
    mark = services.toggle_mark(meeting, intern, user=request.user)
    score = WorkScore.objects.filter(meeting=meeting, intern=intern).first()
    return render(request, 'attendance/partials/cell.html', {
        'intern': intern,
        'cell': {
            'meeting': meeting,
            'status': mark.status if mark else '',
            'score': score.score if score else None,
        },
    })


@login_required
def meeting_mark_all(request, pk):
    """Отметить всю команду присутствующей на собрании."""
    meeting = get_object_or_404(
        GroupMeeting.objects.select_related('group'), pk=pk,
    )
    if request.method == 'POST':
        created = services.mark_all_present(meeting, user=request.user)
        messages.success(request, f'Отмечено присутствующих: {created}.')
    return redirect('attendance:meeting_detail', pk=meeting.pk)


@login_required
def dashboard(request):
    """Табель: собрания сегодня и посещаемость за неделю."""
    today = timezone.localdate()
    raw_week = request.GET.get('week')
    if raw_week:
        try:
            day = datetime.date.fromisoformat(raw_week)
        except ValueError:
            raise Http404('Некорректная неделя')
    else:
        day = today
    week_start, week_end = overview.week_bounds(day)
    prev_week = week_start - datetime.timedelta(days=7)
    next_week = week_start + datetime.timedelta(days=7)

    rows = overview.groups_summary(week_start, week_end)
    total_meetings = sum(row['meetings'] for row in rows)
    total_held = sum(row['held'] for row in rows)
    rates = [row['rate'] for row in rows if row['rate'] is not None]
    activities = [row['activity'] for row in rows if row['activity'] is not None]

    return render(request, 'attendance/dashboard.html', {
        'rows': rows,
        'today_meetings': overview.today_meetings(),
        'week_meetings': overview.period_meetings(week_start, week_end),
        'total_meetings': total_meetings,
        'total_held': total_held,
        'average_rate': round(sum(rates) / len(rates)) if rates else None,
        'average_activity': (
            round(sum(activities) / len(activities), 1) if activities else None
        ),
        'week_start': week_start,
        'week_end': week_end,
        'prev_week': prev_week,
        'next_week': next_week,
        'is_current_week': week_start == overview.week_bounds(today)[0],
        'today': today,
    })


@login_required
def group_meetings(request, pk):
    """Список собраний команды: отсюда заходят отмечать."""
    group = get_object_or_404(
        Group.objects.select_related('flow', 'project'), pk=pk,
    )
    meetings = list(
        group.meetings.select_related('host')
        .prefetch_related('attendance', 'scores')
        .order_by('-date'),
    )
    people = services.attendance_eligible_members(group).filter(
        status='active', intern__isnull=False,
    ).count()
    for meeting in meetings:
        marks = list(meeting.attendance.all())
        meeting.marked_count = len(marks)
        meeting.attended_count = sum(1 for mark in marks if mark.is_attended)
        scores = [item.score for item in meeting.scores.all()]
        meeting.score_count = len(scores)
        meeting.avg_score = (
            round(sum(scores) / len(scores), 1) if scores else None
        )
    return render(request, 'attendance/group_meetings.html', {
        'group': group,
        'meetings': meetings,
        'people': people,
        'today': timezone.localdate(),
    })


@login_required
def meeting_detail(request, pk):
    """Одно собрание: список людей команды и отметки."""
    meeting = get_object_or_404(
        GroupMeeting.objects.select_related('group', 'group__project', 'host'),
        pk=pk,
    )
    members = list(
        services.attendance_eligible_members(meeting.group)
        .select_related('intern__specialization')
        .filter(intern__isnull=False)
        .order_by('role', 'intern__full_name'),
    )
    marks = {
        mark.intern_id: mark for mark in meeting.attendance.all()
    }
    scores = {
        score.intern_id: score for score in meeting.scores.all()
    }
    previous = services.previous_scores(meeting)
    rows = []
    for member in members:
        mark = marks.get(member.intern_id)
        score = scores.get(member.intern_id)
        was = previous.get(member.intern_id)
        value = score.score if score else None
        rows.append({
            'member': member,
            'intern': member.intern,
            'status': mark.status if mark else '',
            'score': value,
            'score_comment': score.comment if score else '',
            'previous': was,
            'delta': (value - was) if value is not None and was is not None else None,
            'segments': services.score_segments(value),
            'level': services.score_level(value),
        })
    # В оценке сначала показываем тех, кому балл ещё не поставили
    score_rows = sorted(rows, key=lambda row: row['score'] is not None)
    attended = sum(
        1 for row in rows
        if row['status'] in ('present', 'late')
    )
    marked = sum(1 for row in rows if row['status'])
    given = [row['score'] for row in rows if row['score'] is not None]
    return render(request, 'attendance/meeting_detail.html', {
        'meeting': meeting,
        'group': meeting.group,
        'rows': rows,
        'marked': marked,
        'attended': attended,
        'rate': round(attended / marked * 100) if marked else None,
        'statuses': Attendance.Status.choices,
        'score_rows': score_rows,
        'scale': WorkScore.SCALE,
        'scored': len(given),
        'average_score': round(sum(given) / len(given), 1) if given else None,
        'period_start': meeting.period_start,
        'period_days': meeting.period_days,
        'tab': 'scores' if request.GET.get('tab') == 'scores' else 'marks',
    })


@login_required
def mark_person(request, pk):
    """AJAX: отметка человека на собрании конкретным статусом."""
    meeting = get_object_or_404(GroupMeeting, pk=pk)
    if request.method != 'POST':
        raise Http404
    intern = get_object_or_404(Intern, pk=request.POST.get('intern'))
    if not services.attendance_eligible_members(meeting.group).filter(
        intern=intern,
    ).exists():
        raise Http404
    status = request.POST.get('status', '')

    mark = Attendance.objects.filter(meeting=meeting, intern=intern).first()
    if status in Attendance.Status.values:
        if mark:
            mark.status = status
            mark.marked_by = request.user
            mark.save(update_fields=['status', 'marked_by', 'updated_at'])
        else:
            Attendance.objects.create(
                meeting=meeting, intern=intern, status=status,
                marked_by=request.user,
            )
        if meeting.status == GroupMeeting.Status.PLANNED:
            meeting.status = GroupMeeting.Status.HELD
            meeting.save(update_fields=['status', 'updated_at'])
    elif mark:
        mark.delete()
        status = ''

    return render(request, 'attendance/partials/person_row.html', {
        'meeting': meeting,
        'row': {'intern': intern, 'status': status,
                'member': meeting.group.members.filter(intern=intern).first()},
    })


@login_required
def score_person(request, pk):
    """AJAX: балл за работу с прошлого собрания (0–10) или комментарий."""
    meeting = get_object_or_404(GroupMeeting, pk=pk)
    if request.method != 'POST':
        raise Http404
    intern = get_object_or_404(Intern, pk=request.POST.get('intern'))
    if not services.attendance_eligible_members(meeting.group).filter(
        intern=intern,
    ).exists():
        raise Http404
    entry = WorkScore.objects.filter(meeting=meeting, intern=intern).first()

    if 'comment' in request.POST:
        comment = request.POST.get('comment', '').strip()[:255]
        if entry:
            entry.comment = comment
            entry.save(update_fields=['comment', 'marked_by', 'updated_at'])
        elif comment:
            entry = WorkScore.objects.create(
                meeting=meeting, intern=intern, score=0,
                comment=comment, marked_by=request.user,
            )
    else:
        raw = request.POST.get('score', '')
        if raw.isdigit() and 0 <= int(raw) <= WorkScore.MAX:
            value = int(raw)
            if entry:
                entry.score = value
                entry.marked_by = request.user
                entry.save(update_fields=['score', 'marked_by', 'updated_at'])
            else:
                entry = WorkScore.objects.create(
                    meeting=meeting, intern=intern, score=value,
                    marked_by=request.user,
                )
        elif entry:
            entry.delete()
            entry = None

    return render(request, 'attendance/partials/score_row.html', {
        'meeting': meeting,
        'scale': WorkScore.SCALE,
        'row': services.score_row(
            meeting, intern,
            score=entry.score if entry else None,
            comment=entry.comment if entry else '',
            previous=services.previous_scores(meeting).get(intern.pk),
        ),
    })

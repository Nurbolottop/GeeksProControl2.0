"""Данные для вкладки «Графика»: путь проекта по этапам и живые цифры."""
import datetime

from django.utils import timezone

from apps.projects.models import ProjectStage, ProjectStageKey


def _shares(count: int) -> list[int]:
    """Доли этапов в процентах, в сумме ровно 100."""
    if not count:
        return []
    base = 100 // count
    shares = [base] * count
    for index in range(100 - base * count):
        shares[index] += 1
    return shares


def _plural(number: int, one: str, few: str, many: str) -> str:
    number = abs(number)
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many


def _days(number: int) -> str:
    return _plural(number, 'день', 'дня', 'дней')


def stage_roadmap(project) -> dict:
    """Этапы проекта дорожкой: что пройдено, где идём сейчас, что впереди.

    Служебный этап «Завершён» в дорожку не берём — он не работа, а
    финишный флажок в конце пути (как и в расчёте прогресса).
    """
    stages = [
        stage for stage in project.stages.all()
        if stage.key != ProjectStageKey.COMPLETED
    ]
    shares = _shares(len(stages))
    current_index = None
    for index, stage in enumerate(stages):
        if stage.status == ProjectStage.Status.IN_PROGRESS:
            current_index = index
            break
    if current_index is None:
        # ни один этап не в работе: встаём на первый незавершённый
        for index, stage in enumerate(stages):
            if stage.status != ProjectStage.Status.DONE:
                current_index = index
                break

    segments = []
    for index, stage in enumerate(stages):
        if stage.status == ProjectStage.Status.DONE:
            state = 'done'
        elif index == current_index:
            state = 'current'
        else:
            state = 'next'
        segments.append({
            'stage': stage,
            'index': index + 1,
            'title': stage.get_key_display(),
            'share': shares[index],
            'state': state,
            'is_current': index == current_index,
            'is_last': index == len(stages) - 1,
            # у текущего этапа — его собственный процент готовности
            'progress': 100 if state == 'done' else stage.progress,
            'date': stage.end_date or stage.deadline,
            'date_is_plan': stage.end_date is None,
        })

    done = sum(1 for stage in stages if stage.status == ProjectStage.Status.DONE)
    return {
        'segments': segments,
        'total': len(stages),
        'done': done,
        'left': len(stages) - done,
        'percent': round(100 * done / len(stages)) if stages else 0,
        'current': segments[current_index] if current_index is not None else None,
        # для анимации маркера: он встаёт на текущий этап и продвигается
        # внутри него на процент готовности этого этапа
        'marker': (
            current_index + (segments[current_index]['progress'] or 0) / 100
            if current_index is not None else len(stages)
        ),
    }


def project_pulse(project, roadmap: dict) -> list[dict]:
    """Главные цифры проекта для плиток: сроки, работа, отчёты, команда."""
    from apps.teams.models import TeamMember

    today = timezone.localdate()
    tiles = []

    deadline = project.planned_end_date
    if deadline:
        left = (deadline - today).days
        if left >= 0:
            tiles.append({
                'value': left, 'unit': _days(left), 'label': 'до дедлайна',
                'hint': deadline.strftime('%d.%m.%Y'),
                'tone': 'red' if left <= 7 else 'yellow' if left <= 21 else 'green',
            })
        else:
            tiles.append({
                'value': -left, 'unit': _days(left), 'label': 'просрочки',
                'hint': f'дедлайн был {deadline:%d.%m.%Y}', 'tone': 'red',
            })

    stage_starts = [
        segment['stage'].start_date for segment in roadmap['segments']
        if segment['stage'].start_date
    ]
    started = project.start_date or (min(stage_starts) if stage_starts else None)
    if not started:
        started = timezone.localtime(project.created_at).date()
    in_work = max((today - started).days, 0)
    tiles.append({
        'value': in_work, 'unit': _days(in_work), 'label': 'в работе',
        'hint': f'с {started:%d.%m.%Y}', 'tone': 'blue',
    })

    # отчёт ПМ сдаётся раз в неделю: неделя — норма, две — тревожно
    reports = project.reports.all()
    last_report = reports.first()
    if last_report:
        ago = max((today - last_report.date).days, 0)
        tiles.append({
            'value': ago, 'unit': _days(ago) + ' назад', 'label': 'последний отчёт',
            'hint': (
                f'{last_report.date:%d.%m.%Y} · всего {reports.count()}'
            ),
            'tone': 'green' if ago <= 7 else 'yellow' if ago <= 14 else 'red',
        })
    else:
        tiles.append({
            'text': 'нет', 'label': 'последний отчёт',
            'hint': 'ПМ ещё не сдавал отчёт', 'tone': 'red',
        })

    team = project.team_members.filter(status=TeamMember.Status.ACTIVE).count()
    tiles.append({
        'value': team, 'unit': _plural(team, 'человек', 'человека', 'человек'),
        'label': 'в команде', 'hint': 'сейчас на проекте', 'tone': 'teal',
    })
    return tiles


def stage_timeline(roadmap: dict) -> list[dict]:
    """Хронология: когда какой этап закрыт или должен закрыться."""
    today = timezone.localdate()
    rows = []
    for segment in roadmap['segments']:
        stage = segment['stage']
        if not (stage.start_date or stage.end_date or stage.deadline):
            continue
        overdue = (
            segment['state'] != 'done' and stage.deadline
            and stage.deadline < today
        )
        rows.append({
            'segment': segment,
            'start': stage.start_date,
            'end': stage.end_date,
            'deadline': stage.deadline,
            'overdue': overdue,
        })
    return rows


def road_data(roadmap: dict) -> dict:
    """То, что скрипт дороги этапов берёт из json_script на странице."""
    return {
        'stages': [
            {'title': s['title'], 'state': s['state'], 'index': s['index']}
            for s in roadmap['segments']
        ],
        'marker': roadmap['marker'],
    }


def graphics_context(project) -> dict:
    """Всё для вкладки «Графика»: путь, плитки, хронология, данные дороги.

    Одна сборка на все места, где графика показывается, — основное
    приложение и портал ПМ, чтобы картинка нигде не разъезжалась.
    """
    roadmap = stage_roadmap(project)
    return {
        'roadmap': roadmap,
        'pulse': project_pulse(project, roadmap),
        'timeline': stage_timeline(roadmap),
        'road_data': road_data(roadmap),
    }

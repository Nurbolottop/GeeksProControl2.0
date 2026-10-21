"""Проверка исторических нарушений правил состава (UI-04B). Только отчёт —
ничего не удаляет и не меняет. Решение по каждому случаю — за руководителем.

Правила (для всех направлений, включая PM):
* новичок — не больше 1 активного проекта;
* выпускник — не больше 2 активных проектов;
* в одном направлении проекта — не больше 4 выпускников.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand


def violations():
    from apps.dashboard.graduation import compute
    from apps.teams.models import TeamMember
    from apps.teams.rules import NEW_MAX_PROJECTS, OLD_MAX_PER_DIRECTION, OLD_MAX_PROJECTS, RULE_ROLES
    from apps.teams.selectors import lead_intern_ids

    leads = lead_intern_ids()
    rows = list(TeamMember.objects.filter(status=TeamMember.Status.ACTIVE, role__in=RULE_ROLES, intern__isnull=False,
                                          project__isnull=False).exclude(intern_id__in=leads)
                .select_related('intern', 'project'))
    graduates = compute({m.intern_id for m in rows}).ids
    by_person, by_direction = defaultdict(set), defaultdict(set)
    names, projects = {}, {}
    for m in rows:
        by_person[m.intern_id].add(m.project_id)
        names[m.intern_id] = m.intern.full_name
        projects[m.project_id] = m.project.name
        if m.intern_id in graduates:
            by_direction[(m.project_id, m.get_role_display())].add(m.intern_id)
    found = []
    for pk, items in by_person.items():
        limit = OLD_MAX_PROJECTS if pk in graduates else NEW_MAX_PROJECTS
        if len(items) > limit:
            kind = 'выпускник' if pk in graduates else 'новичок'
            found.append(f'{names[pk]} ({kind}): активных проектов {len(items)} при лимите {limit} — '
                         + ', '.join(sorted(projects[p] for p in items)))
    for (project_id, role), people in by_direction.items():
        if len(people) > OLD_MAX_PER_DIRECTION:
            found.append(f'«{projects[project_id]}», {role}: выпускников {len(people)} при лимите {OLD_MAX_PER_DIRECTION}')
    return sorted(found)


class Command(BaseCommand):
    help = 'Отчёт о нарушениях правил состава (ничего не меняет).'

    def handle(self, *args, **options):
        found = violations()
        if not found:
            self.stdout.write('Нарушений правил состава нет.')
            return
        self.stdout.write(f'Нарушений: {len(found)} (данные не изменены — решение за руководителем)')
        for line in found:
            self.stdout.write(f'  {line}')

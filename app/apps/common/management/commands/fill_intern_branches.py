"""Проставить филиал стажёрам, у которых он не определяется.

Часть карточек заводили вручную, и филиал с городом остались пустыми.
Единственная зацепка — города проектов, на которых человек работает:
если все его проекты в одном городе, берём филиал оттуда. Если проекты
в разных городах или город проекта не указан — пропускаем, такие случаи
разбираются руками.

По умолчанию только показывает, что будет сделано; запись — с --apply.
"""
from django.core.management.base import BaseCommand

from apps.interns import services
from apps.interns.models import Intern
from apps.teams.models import TeamMember


class Command(BaseCommand):
    help = 'Проставляет филиал стажёрам по городам их проектов.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Записать изменения (без флага — только показать).',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        interns = [
            intern for intern in Intern.objects.active().select_related('specialization')
            if services.branch_of(intern) == services.BRANCH_UNKNOWN
        ]
        if not interns:
            self.stdout.write('Стажёров без филиала нет.')
            return

        projects_by_intern: dict[int, list] = {}
        memberships = (
            TeamMember.objects
            .filter(intern__in=interns, status=TeamMember.Status.ACTIVE,
                    project__isnull=False)
            .select_related('project')
        )
        for member in memberships:
            projects_by_intern.setdefault(member.intern_id, []).append(member.project)

        filled, skipped = 0, []
        for intern in sorted(interns, key=lambda i: i.full_name):
            projects = projects_by_intern.get(intern.pk, [])
            branches = {
                services.branch_from_text(project.city) for project in projects
            } - {services.BRANCH_UNKNOWN}
            if len(branches) != 1:
                reason = (
                    'нет проектов' if not projects
                    else 'город проекта не указан' if not branches
                    else 'проекты в разных филиалах: ' + ', '.join(sorted(branches))
                )
                skipped.append((intern, reason))
                continue
            branch = branches.pop()
            source = ', '.join(project.name for project in projects)
            self.stdout.write(f'{intern.full_name}: {branch} (по проектам: {source})')
            if apply:
                intern.branch = branch
                intern.save(update_fields=['branch', 'updated_at'])
            filled += 1

        for intern, reason in skipped:
            self.stdout.write(self.style.WARNING(
                f'пропущен {intern.full_name}: {reason}',
            ))
        verb = 'Проставлен филиал' if apply else 'Будет проставлен филиал'
        self.stdout.write(self.style.SUCCESS(
            f'{verb}: {filled}. Пропущено: {len(skipped)}.',
        ))
        if not apply:
            self.stdout.write('Это предпросмотр. Для записи: --apply')

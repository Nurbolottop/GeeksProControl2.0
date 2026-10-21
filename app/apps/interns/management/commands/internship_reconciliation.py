"""Сверка периодов стажировок (UI-04C). Только отчёт — даты не угадываются.

Показывает неоднозначные записи для решения руководителя:
* активное участие в проекте без открытого периода;
* открытый период у отчисленного / отменившего / выбывшего;
* период без даты начала;
* закрытый период без даты окончания;
* выпускник без даты выпуска в периоде.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Сверка периодов стажировок (ничего не меняет).'

    def handle(self, *args, **options):
        from apps.dashboard.graduation import compute
        from apps.interns.models import Intern, Internship, InternStatus
        from apps.teams.models import TeamMember
        from apps.teams.selectors import lead_intern_ids

        leads = lead_intern_ids()
        lines = []
        open_ids = set(Internship.objects.filter(ended_on__isnull=True, end_reason='').values_list('intern_id', flat=True))
        for intern in (Intern.objects.filter(team_memberships__status=TeamMember.Status.ACTIVE)
                       .exclude(pk__in=leads).exclude(pk__in=open_ids).distinct()):
            lines.append(f'{intern.full_name}: в проекте, но открытого периода нет')
        stopped = (InternStatus.EXPELLED, InternStatus.CANCELLED, InternStatus.DROPPED)
        for period in Internship.objects.filter(ended_on__isnull=True, end_reason='', intern__status__in=stopped).select_related('intern'):
            lines.append(f'{period.intern.full_name}: открыт период №{period.number}, статус «{period.intern.get_status_display()}»')
        for period in Internship.objects.filter(started_on__isnull=True).select_related('intern'):
            lines.append(f'{period.intern.full_name}: у периода №{period.number} нет даты начала')
        for period in Internship.objects.exclude(end_reason='').filter(ended_on__isnull=True).select_related('intern'):
            lines.append(f'{period.intern.full_name}: период №{period.number} закрыт ({period.get_end_reason_display()}) без даты')
        graduates = compute().ids - leads
        dated = set(Internship.objects.filter(graduated_on__isnull=False).values_list('intern_id', flat=True))
        for intern in Intern.objects.filter(pk__in=graduates - dated).order_by('full_name'):
            lines.append(f'{intern.full_name}: выпускник, но дата выпуска в периоде не записана')
        if not lines:
            self.stdout.write('Расхождений в периодах нет.')
            return
        self.stdout.write(f'Записей на сверку: {len(lines)} (ничего не изменено)')
        for line in lines:
            self.stdout.write(f'  {line}')

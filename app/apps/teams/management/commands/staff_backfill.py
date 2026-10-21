"""Кадровые поля 2.0 для сотрудников из 1.0 (этап 11D).

В 1.0 сотрудник определялся должностью (Intern.position) и ролью учётки;
поля staff_status / staff_role появились только в 2.0 и у перенесённых
людей пусты. Из-за этого бот не показывает тимлидам и ПМ их меню, а ПМ
считаются стажёрами в списках и показателях.

  manage.py staff_backfill           — отчёт, ничего не меняет
  manage.py staff_backfill --apply   — заполнить только однозначные случаи

Однозначно, если сходятся все признаки 1.0:
* тимлид — position=lead, учётка с ролью team_lead, учётка активна;
* ПМ — position=pm, учётка с ролью pm, учётка активна и есть активное
  назначение ПМ на проект.
staff_since не заполняется: даты начала работы в 1.0 нет. Остальные
случаи (признаки расходятся) только перечисляются — решение за руководителем.
Каждое изменение пишется в журнал; повторный запуск ничего не делает.
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Кадровый статус 2.0 для тимлидов и ПМ из 1.0 (только однозначные случаи).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Заполнить однозначные случаи.')

    def handle(self, *args, apply=False, **options):
        from apps.audit.services import log as audit_log
        from apps.interns.models import Intern, Position, StaffRole, StaffStatus
        from apps.teams.models import TeamMember, TeamRole

        people = (Intern.objects.filter(staff_status='', user__role__in=['pm', 'team_lead'])
                  .select_related('user').order_by('pk'))
        sure, unclear = [], []
        for intern in people:
            user = intern.user
            active_pm = TeamMember.objects.filter(intern=intern, role=TeamRole.PROJECT_MANAGER,
                                                  status=TeamMember.Status.ACTIVE).exists()
            if (user.role == 'team_lead' and intern.position == Position.TEAM_LEAD and user.is_active
                    and not intern.is_archived):
                sure.append((intern, StaffRole.LEAD))
            elif (user.role == 'pm' and intern.position == Position.PM and user.is_active and active_pm
                  and not intern.is_archived):
                sure.append((intern, StaffRole.PM))
            else:
                why = []
                if intern.position != {'pm': Position.PM, 'team_lead': Position.TEAM_LEAD}[user.role]:
                    why.append(f'должность «{intern.get_position_display()}» не совпадает с учёткой')
                if not user.is_active:
                    why.append('учётка отключена')
                if user.role == 'pm' and not active_pm:
                    why.append('нет активного назначения ПМ')
                if intern.is_archived:
                    why.append('в архиве')
                unclear.append((intern, '; '.join(why)))
        self.stdout.write(f'Однозначно сотрудники: {len(sure)} '
                          f'(тимлидов {sum(r == StaffRole.LEAD for _, r in sure)}, ПМ {sum(r == StaffRole.PM for _, r in sure)})')
        for intern, role in sure:
            self.stdout.write(f'  id={intern.pk} → {StaffRole(role).label}, {StaffStatus.ACTIVE.label}')
        self.stdout.write(f'Требуют решения руководителя: {len(unclear)}')
        for intern, why in unclear:
            self.stdout.write(f'  id={intern.pk} (учётка {intern.user.role}): {why}')
        if not apply:
            self.stdout.write('Ничего не изменено — запустите с --apply.')
            return
        changed = 0
        for intern, role in sure:
            with transaction.atomic():
                locked = Intern.objects.select_for_update().get(pk=intern.pk)
                if locked.staff_status:
                    continue
                locked.staff_status, locked.staff_role = StaffStatus.ACTIVE, role
                locked.save(update_fields=['staff_status', 'staff_role', 'updated_at'])
                audit_log(locked, 'Кадровый статус заполнен при переносе из 1.0',
                          new_value=f'{StaffRole(role).label} · {StaffStatus.ACTIVE.label}',
                          reason='сверка: должность и учётка 1.0 совпадают; дата начала неизвестна')
                changed += 1
        self.stdout.write(f'Заполнено: {changed}')

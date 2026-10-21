"""Периоды стажировок (UI-04A). Один профиль — несколько периодов.

* Период открывается при первом фактическом вступлении в проект (или
  берётся дата из карточки, если она заполнена до вступления).
* Заморозка и возвращение (apps.interns.freeze) — внутри открытого периода.
* Закрывают период только явные события (UI-04B: отчисление, отмена,
  завершение, трудоустройство) — функцией close().
* Повторное зачисление (UI-04B) открывает следующий номер — профиль тот же.
"""
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.interns.models import Intern, Internship


def current(intern) -> Internship | None:
    return intern.internships.filter(ended_on__isnull=True, end_reason='').order_by('-number').first()


def ensure_open(intern: Intern, started_on=None, source=Internship.Source.JOIN) -> Internship:
    """Открытый период стажировки; создаётся, если его ещё нет."""
    existing = current(intern)
    if existing is not None:
        return existing
    last = intern.internships.order_by('-number').first()
    number = last.number + 1 if last else max(intern.internship_attempt or 1, 1)
    if last is None and intern.internship_start_date:
        started_on, source = intern.internship_start_date, Internship.Source.CARD
    try:
        with transaction.atomic():
            return Internship.objects.create(intern=intern, number=number,
                                             started_on=started_on or timezone.localdate(), source=source)
    except IntegrityError:
        # Параллельное вступление уже открыло период — берём его
        return current(intern)


def close(internship: Internship, reason: str, ended_on=None, note: str = '') -> Internship:
    if not internship.is_open:
        return internship
    internship.ended_on = ended_on or timezone.localdate()
    internship.end_reason = reason
    internship.note = note[:255]
    internship.save(update_fields=['ended_on', 'end_reason', 'note', 'updated_at'])
    return internship


def on_membership_saved(sender, instance, **kwargs):
    """Сигнал TeamMember.post_save: стажёр вступил в проект — период открыт."""
    from apps.teams.models import TeamMember, TeamRole
    from apps.teams.selectors import lead_intern_ids

    if instance.status != TeamMember.Status.ACTIVE or not instance.intern_id or not instance.project_id:
        return
    if instance.role == TeamRole.TEAM_LEAD or instance.intern_id in lead_intern_ids():
        return  # тимлид — сотрудник, не стажёр
    ensure_open(instance.intern, started_on=instance.joined_at)

"""Переезд старого пула «Резерв кадров» в полноценный модуль.

Раньше резерв был плоским списком (interns.TalentReserveCandidate:
ФИО, контакты, желаемая роль, опыт, портфолио). Переносим эти записи в
новую карточку кандидата, чтобы ничего не потерялось и не пришлось
вбивать людей заново. Старая таблица остаётся нетронутой.
"""
from django.db import migrations


def copy_candidates(apps, schema_editor):
    Old = apps.get_model('interns', 'TalentReserveCandidate')
    New = apps.get_model('reserve', 'ReserveCandidate')
    Event = apps.get_model('reserve', 'ReserveEvent')
    for old in Old.objects.all():
        if New.objects.filter(full_name=old.full_name, phone=old.phone).exists():
            continue
        candidate = New.objects.create(
            full_name=old.full_name,
            phone=old.phone,
            email=old.email,
            telegram=old.telegram,
            city=old.city,
            specialization_id=old.specialization_id,
            desired_position=old.desired_role,
            work_experience=old.experience,
            portfolio_url=old.portfolio_link,
            decision_comment=old.comment,
            status='reserve',
            is_archived=old.is_archived,
            archived_at=old.archived_at,
        )
        Event.objects.create(
            candidate=candidate, kind='created',
            title='Перенесён(а) из прежнего списка резерва',
            detail='Автоматический перенос при запуске раздела «Резерв кадров»',
        )


def noop(apps, schema_editor):
    """Обратно ничего не удаляем: старые записи на месте, новые — данные."""


class Migration(migrations.Migration):

    dependencies = [
        ('reserve', '0001_initial'),
        ('interns', '0010_profileformlink_expires_at_profileformsubmission'),
    ]

    operations = [
        migrations.RunPython(copy_candidates, noop),
    ]

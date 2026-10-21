"""Проставляет направление уже созданным собраниям — по тому, кто их вёл.

Раньше собрание принадлежало всей команде: тимлид дизайна создавал
встречу, и она же висела у бэкенда и фронтенда, требуя от них отметок.
Теперь у собрания есть направление; у старых берём его из специализации
ведущего, а если ведущий не указан — оставляем общим.
"""
from django.db import migrations

# Специализация человека → роль в команде (копия apps.teams.forms.ROLE_BY_SPECIALIZATION:
# в миграции нельзя зависеть от кода, который потом поменяется)
ROLE_BY_SPECIALIZATION = {
    'Backend': 'backend',
    'Frontend': 'frontend',
    'Mobile': 'mobile',
    'UX/UI': 'uxui',
    'Testing/QA': 'qa',
}


def set_direction(apps, schema_editor):
    GroupMeeting = apps.get_model('attendance', 'GroupMeeting')
    meetings = GroupMeeting.objects.filter(
        direction='', host__isnull=False,
    ).select_related('host__specialization')
    for meeting in meetings:
        spec = meeting.host.specialization
        role = ROLE_BY_SPECIALIZATION.get(spec.name) if spec else None
        if role:
            meeting.direction = role
            meeting.save(update_fields=['direction'])


def clear_direction(apps, schema_editor):
    GroupMeeting = apps.get_model('attendance', 'GroupMeeting')
    GroupMeeting.objects.update(direction='')


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0004_alter_groupmeeting_unique_together_and_more'),
    ]

    operations = [
        migrations.RunPython(set_direction, clear_direction),
    ]

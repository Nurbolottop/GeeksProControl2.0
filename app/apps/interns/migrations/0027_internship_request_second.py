from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('interns', '0026_internship_request_continue'),
    ]

    operations = [
        migrations.AlterField(
            model_name='internshiprequest',
            name='kind',
            field=models.CharField(choices=[('freeze', 'Заморозка'), ('return', 'Возвращение'), ('cancel', 'Добровольная отмена'), ('reenroll', 'Повторная стажировка'), ('leave', 'Выход из одного проекта'), ('enroll', 'Новая стажировка (регистрация в боте)'), ('continue', 'Продолжение стажировки (выпускник)'), ('second', 'Участие во втором проекте')], max_length=10, verbose_name='Вид'),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0022_project_intern_brief'),
    ]

    operations = [
        migrations.AlterField(
            model_name='projectstatushistory',
            name='old_value',
            field=models.TextField(blank=True, verbose_name='Старое значение'),
        ),
        migrations.AlterField(
            model_name='projectstatushistory',
            name='new_value',
            field=models.TextField(blank=True, verbose_name='Новое значение'),
        ),
    ]

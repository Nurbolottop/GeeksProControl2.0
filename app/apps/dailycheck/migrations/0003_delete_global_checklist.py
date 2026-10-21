"""Общий чек-лист «Ежедневная проверка» убран — остаются пункты по проектам."""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("dailycheck", "0002_projectcheckitem_projectcheckmark"),
    ]

    operations = [
        migrations.AlterUniqueTogether(name="checkmark", unique_together=set()),
        migrations.RemoveField(model_name="checkmark", name="item"),
        migrations.RemoveField(model_name="checkmark", name="checked_by"),
        migrations.DeleteModel(name="CheckMark"),
        migrations.DeleteModel(name="CheckItem"),
    ]

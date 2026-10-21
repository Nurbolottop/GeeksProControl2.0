from django import forms


class LeadToReserveForm(forms.Form):
    """Что тимлид добавляет к карточке кандидата, отправляя его в резерв.

    Только комментарий, и тот по желанию: навыки и остальное резюме
    кандидат заполняет сам.
    """

    comment = forms.CharField(
        label='Комментарий тимлида', required=False,
        help_text='Как он(а) работает в команде — видят только сотрудники.',
        widget=forms.Textarea(attrs={'rows': 4}),
    )

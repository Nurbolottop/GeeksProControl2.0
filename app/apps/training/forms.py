from django import forms

from apps.training.models import BRANCHES, GroupStatus, TrainingGroup


class TrainingGroupForm(forms.ModelForm):
    """Группа академии. Стадия считается по датам — руками только «не состоялась»."""

    cancelled = forms.BooleanField(
        label='Группа не состоялась', required=False,
        help_text='Такая группа не попадает в план-график.',
    )

    class Meta:
        model = TrainingGroup
        fields = [
            'number', 'specialization', 'branch', 'start_date', 'end_date',
            'students_count', 'students_note',
            'wants_internship', 'expected_interns', 'actual_interns',
            'teacher', 'comment',
        ]
        widgets = {
            'number': forms.TextInput(attrs={'placeholder': 'Например: 44'}),
            'start_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'end_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'students_note': forms.TextInput(attrs={'placeholder': '5–7, на старте…'}),
            'comment': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cancelled'].initial = self.instance.status == GroupStatus.CANCELLED
        for name in ('wants_internship', 'expected_interns', 'actual_interns'):
            self.fields[name].required = False

    def clean(self):
        data = super().clean()
        start, end = data.get('start_date'), data.get('end_date')
        if start and end and end < start:
            self.add_error('end_date', 'Выпуск не может быть раньше начала обучения.')
        for name in ('wants_internship', 'expected_interns', 'actual_interns'):
            if data.get(name) is None:
                data[name] = 0
        students = data.get('students_count')
        if students is not None:
            for name in ('wants_internship', 'expected_interns', 'actual_interns'):
                if data[name] > students:
                    self.add_error(name, 'Не может быть больше, чем обучающихся в группе.')
        return data

    def save(self, commit=True):
        group = super().save(commit=False)
        if self.cleaned_data.get('cancelled'):
            group.status = GroupStatus.CANCELLED
        elif group.status == GroupStatus.CANCELLED:
            # сняли галочку — пусть стадия снова считается по датам
            group.status = GroupStatus.STUDYING
        if commit:
            group.save()
        return group


class ImportForm(forms.Form):
    branch = forms.ChoiceField(
        label='Филиал', choices=BRANCHES, widget=forms.RadioSelect,
        help_text='Если в тексте есть строка «Бишкек» или «Ош», группы ниже неё уйдут в этот филиал.',
    )
    text = forms.CharField(
        label='Сообщение от академии',
        widget=forms.Textarea(attrs={
            'rows': 16,
            'placeholder': (
                '🎨 Design\n'
                '* 39 группа — старт: 09.06.2026 · конец: 06.10.2026 — 4 студента\n\n'
                '⚙️Backend\n'
                '* 44 группа — старт: 23.09.2026 · конец: 10.03.2027 — 11 студентов'
            ),
        }),
    )

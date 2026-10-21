from django import forms

from apps.interns.models import Intern, InternEvaluation, TalentReserveCandidate


class InternForm(forms.ModelForm):
    class Meta:
        model = Intern
        fields = [
            'full_name', 'phone', 'email', 'telegram', 'city', 'branch',
            'specialization', 'education_end_date',
            'internship_start_date', 'internship_attempt', 'status', 'comment',
        ]
        widgets = {
            'education_end_date': forms.DateInput(
                attrs={'type': 'date'}, format='%Y-%m-%d',
            ),
            'internship_start_date': forms.DateInput(
                attrs={'type': 'date'}, format='%Y-%m-%d',
            ),
            'comment': forms.Textarea(attrs={'rows': 2}),
        }

    # Эти статусы ставят только процедуры (отчисление, отмена — UI-04B;
    # трудоустройство — подтверждение руководителя, этап 6B), не анкета
    PROCESS_STATUSES = ('expelled', 'cancelled', 'employed')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current = self.instance.status if self.instance.pk else None
        self.fields['status'].choices = [c for c in self.fields['status'].choices
                                         if c[0] not in self.PROCESS_STATUSES or c[0] == current]

    def clean_status(self):
        status = self.cleaned_data.get('status')
        current = self.instance.status if self.instance.pk else None
        if status != current and (status in self.PROCESS_STATUSES or current in self.PROCESS_STATUSES):
            raise forms.ValidationError('Этот статус меняется только соответствующей процедурой, не анкетой.')
        return status


class ResumeBankApplyForm(forms.ModelForm):
    """Публичная анкета «Банк резюме» — человек заполняет сам, без входа."""

    class Meta:
        model = Intern
        fields = ['full_name', 'phone', 'email', 'specialization']
        labels = {'specialization': 'Направление'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['phone'].required = True


class ProfileApplyForm(forms.ModelForm):
    """Публичная анкета: стажёр сам заполняет/обновляет свой профиль."""

    class Meta:
        model = Intern
        fields = [
            'full_name', 'phone', 'email', 'telegram', 'city', 'branch',
            'specialization', 'education_end_date',
            'internship_start_date', 'internship_attempt',
        ]
        labels = {'specialization': 'Направление'}
        widgets = {
            'education_end_date': forms.DateInput(
                attrs={'type': 'date'}, format='%Y-%m-%d',
            ),
            'internship_start_date': forms.DateInput(
                attrs={'type': 'date'}, format='%Y-%m-%d',
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = True


class TalentReserveApplyForm(forms.ModelForm):
    """Публичная анкета «Резерв кадров» — человек заполняет сам, без входа."""

    class Meta:
        model = TalentReserveCandidate
        fields = [
            'full_name', 'phone', 'email', 'telegram', 'city',
            'specialization', 'desired_role', 'experience', 'portfolio_link',
        ]
        labels = {'specialization': 'Направление'}
        widgets = {
            'experience': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['phone'].required = True


class TalentReserveForm(forms.ModelForm):
    """Карточка кандидата в резерве — заполняет/правит сотрудник."""

    class Meta:
        model = TalentReserveCandidate
        fields = [
            'full_name', 'phone', 'email', 'telegram', 'city',
            'specialization', 'desired_role', 'experience', 'portfolio_link',
            'priority', 'comment',
        ]
        widgets = {
            'experience': forms.Textarea(attrs={'rows': 3}),
            'comment': forms.Textarea(attrs={'rows': 2}),
        }


class GrantAccessForm(forms.Form):
    """Выдать/сбросить доступ ПМу — логином служит телефон."""

    username = forms.CharField(label='Телефон (логин)', max_length=150)
    password = forms.CharField(label='Пароль', widget=forms.PasswordInput)


class InternEvaluationForm(forms.ModelForm):
    class Meta:
        model = InternEvaluation
        fields = [
            'project', 'hard_skills', 'quality', 'speed', 'responsibility',
            'communication', 'teamwork', 'independence', 'comment',
        ]
        widgets = {
            'comment': forms.Textarea(attrs={'rows': 2}),
        }

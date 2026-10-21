from django import forms

from apps.clients.models import Client
from apps.reserve.models import (
    MANUAL_STATUSES, CandidateStatus, ReserveCandidate, ReserveRecommendation,
)

DATE = forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')

# Что кандидат заполняет о себе сам — один список на анкету и на
# внутреннюю форму, чтобы поля не разъезжались.
PUBLIC_FIELDS = [
    'full_name', 'birth_date', 'city', 'phone', 'telegram', 'email', 'photo',
    'specialization', 'direction_other', 'desired_position', 'expected_level',
    'skills', 'work_experience', 'internship_experience', 'education',
    'courses', 'english_level', 'other_languages', 'about',
    'resume_file', 'resume_url', 'github_url', 'linkedin_url',
    'portfolio_url', 'behance_url', 'website_url', 'project_links',
    'is_looking_for_job', 'work_format', 'employment_type',
    'ready_for_internship', 'ready_to_relocate', 'available_from',
    'desired_salary', 'target_positions',
    'training_group', 'study_specialization', 'teacher', 'curator',
    'study_start', 'study_end',
]

TEXT_WIDGETS = {
    'skills': forms.Textarea(attrs={'rows': 2}),
    'work_experience': forms.Textarea(attrs={'rows': 3}),
    'internship_experience': forms.Textarea(attrs={'rows': 2}),
    'education': forms.Textarea(attrs={'rows': 2}),
    'courses': forms.Textarea(attrs={'rows': 2}),
    'about': forms.Textarea(attrs={'rows': 3}),
    'project_links': forms.Textarea(attrs={'rows': 2}),
    'target_positions': forms.Textarea(attrs={'rows': 2}),
    'birth_date': DATE,
    'available_from': DATE,
    'study_start': DATE,
    'study_end': DATE,
}


class ReserveApplyForm(forms.ModelForm):
    """Публичная анкета кандидата — заполняется по персональной ссылке.

    Внутренних данных (оценки, статусы, комментарии сотрудников) здесь
    нет и быть не может: форма знает только про поля самого кандидата.
    """

    consent_given = forms.BooleanField(
        required=True,
        label=(
            'Я согласен на обработку моих данных и передачу моего резюме и '
            'профессиональной информации потенциальным работодателям'
        ),
        error_messages={'required': 'Без согласия отправить анкету нельзя.'},
    )

    class Meta:
        model = ReserveCandidate
        fields = PUBLIC_FIELDS + ['consent_given']
        widgets = TEXT_WIDGETS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['phone'].required = True
        self.fields['specialization'].empty_label = 'Другое / не из списка'
        self.fields['training_group'].empty_label = 'Не помню / не из GeeksPro'
        self.fields['study_specialization'].empty_label = 'Не выбрано'


class ReserveCandidateForm(forms.ModelForm):
    """Карточка кандидата глазами сотрудника: анкетные поля + учебная часть."""

    class Meta:
        model = ReserveCandidate
        fields = PUBLIC_FIELDS + ['intern', 'status']
        widgets = TEXT_WIDGETS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['full_name'].required = True
        self.fields['specialization'].empty_label = 'Другое / не из списка'
        self.fields['intern'].empty_label = 'Не привязан к стажёру'
        self.fields['intern'].help_text = (
            'Проекты, роли и периоды подтянутся из команд этого стажёра.'
        )
        # «Трудоустроен» — только подтверждением руководителя (этап 6B)
        labels = dict(CandidateStatus.choices)
        current = self.instance.status if self.instance.pk else None
        values = [v for v in [current, *MANUAL_STATUSES, CandidateStatus.NEW] if v]
        if current == CandidateStatus.EMPLOYED:
            values = [CandidateStatus.EMPLOYED]
        self.fields['status'].choices = [(v, labels[v]) for v in dict.fromkeys(values)]

    def clean_status(self):
        status = self.cleaned_data.get('status')
        current = self.instance.status if self.instance.pk else None
        if (status == CandidateStatus.EMPLOYED) != (current == CandidateStatus.EMPLOYED):
            raise forms.ValidationError('Трудоустройство подтверждает руководитель отдельным действием.')
        return status


class ReserveEvaluationForm(forms.ModelForm):
    """Внутренняя оценка — кандидат её не видит и не заполняет."""

    class Meta:
        model = ReserveCandidate
        fields = [field for field, _ in ReserveCandidate.EVALUATION_CRITERIA] + [
            'geekspro_level', 'recommended_position', 'strengths', 'improvements',
            'comment_teacher', 'comment_pm', 'comment_lead', 'comment_curator',
            'decision', 'decision_comment',
        ]
        widgets = {
            'strengths': forms.Textarea(attrs={'rows': 2}),
            'improvements': forms.Textarea(attrs={'rows': 2}),
            'comment_teacher': forms.Textarea(attrs={'rows': 2}),
            'comment_pm': forms.Textarea(attrs={'rows': 2}),
            'comment_lead': forms.Textarea(attrs={'rows': 2}),
            'comment_curator': forms.Textarea(attrs={'rows': 2}),
            'decision_comment': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field, _ in ReserveCandidate.EVALUATION_CRITERIA:
            self.fields[field].widget = forms.NumberInput(
                attrs={'min': 1, 'max': 10, 'step': 1, 'placeholder': '1–10'},
            )


class ReserveRecommendationForm(forms.ModelForm):
    """Рекомендация кандидата компании."""

    class Meta:
        model = ReserveRecommendation
        fields = ['company', 'company_name', 'vacancy', 'sent_on', 'status', 'comment']
        widgets = {
            'sent_on': DATE,
            'comment': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['company'].queryset = Client.objects.active().order_by('organization')
        self.fields['company'].empty_label = 'Компания вне базы заказчиков'
        if not self.initial.get('sent_on'):
            from django.utils import timezone

            self.initial['sent_on'] = timezone.localdate()

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('company') and not cleaned.get('company_name'):
            raise forms.ValidationError(
                'Выберите компанию из базы или впишите название.',
            )
        return cleaned


class StatusChangeForm(forms.Form):
    """Смена статуса кандидата с обязательным следом в истории."""

    status = forms.ChoiceField(choices=CandidateStatus.choices, label='Новый статус')
    comment = forms.CharField(
        label='Комментарий', required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
    )

    def __init__(self, *args, current=None, **kwargs):
        """В списке — только те статусы, что ставят руками.

        Статусы воронки приходят сами из рекомендаций компаниям; текущий
        статус показываем всегда, иначе его случайно собьют выбором.
        """
        super().__init__(*args, **kwargs)
        labels = dict(CandidateStatus.choices)
        values = list(MANUAL_STATUSES)
        if current and current not in values:
            values.insert(0, current)
        self.fields['status'].choices = [(value, labels[value]) for value in values]


class InviteForm(forms.Form):
    """Персональная ссылка на анкету."""

    TTL_CHOICES = [
        ('7', '7 дней'), ('3', '3 дня'), ('14', '14 дней'),
        ('30', '30 дней'), ('', 'Без срока'),
    ]

    recipient = forms.CharField(
        label='Кому отправляем', max_length=255, required=False,
        help_text='ФИО или контакт — чтобы помнить, кому ушла ссылка.',
    )
    ttl_days = forms.ChoiceField(
        label='Срок действия', choices=TTL_CHOICES, required=False, initial='7',
    )

    def ttl(self) -> int | None:
        raw = self.cleaned_data.get('ttl_days') or ''
        return int(raw) if raw.isdigit() else None


class ShareLinkForm(forms.Form):
    """Ссылка на профиль кандидата для работодателя."""

    TTL_CHOICES = [
        ('30', '30 дней'), ('14', '14 дней'), ('7', '7 дней'),
        ('90', '90 дней'), ('', 'Без срока'),
    ]

    recipient = forms.CharField(
        label='Для кого', max_length=255, required=False,
        help_text='Компания или контакт — чтобы видеть, кому ушёл профиль.',
    )
    ttl_days = forms.ChoiceField(
        label='Срок действия', choices=TTL_CHOICES, required=False, initial='30',
    )
    show_contacts = forms.BooleanField(label='Показывать контакты', required=False)

    def ttl(self) -> int | None:
        raw = self.cleaned_data.get('ttl_days') or ''
        return int(raw) if raw.isdigit() else None


class ExternalCandidateForm(forms.ModelForm):
    """Внешний кандидат в кадровый резерв (этап 6B) — публичная форма без входа.

    Только данные самого кандидата; статус, оценки и связь с профилем
    стажёра задают сотрудники. Резюме — те же ограничения файлов, что у документов.
    """

    consent_given = forms.BooleanField(
        required=True, label='Я согласен на обработку моих данных для рассмотрения кандидатуры',
        error_messages={'required': 'Без согласия отправить заявку нельзя.'},
    )
    website = forms.CharField(required=False, label='Сайт (не заполняйте)')  # ловушка для ботов

    class Meta:
        model = ReserveCandidate
        fields = ['full_name', 'phone', 'telegram', 'specialization', 'direction_other', 'work_experience',
                  'resume_file', 'resume_url', 'consent_given']
        labels = {'specialization': 'Направление', 'direction_other': 'Специализация (если нет в списке)',
                  'work_experience': 'Коротко об опыте', 'telegram': 'Telegram или другой контакт'}
        widgets = {'work_experience': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('full_name', 'phone', 'work_experience'):
            self.fields[name].required = True
        self.fields['specialization'].empty_label = 'Другое / не из списка'

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '')
        if len(''.join(ch for ch in phone if ch.isdigit())) < 9:
            raise forms.ValidationError('Укажите телефон полностью.')
        return phone

    def clean_resume_file(self):
        from apps.documents.forms import validate_upload_file

        return validate_upload_file(self.cleaned_data.get('resume_file'))

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('specialization') and not (cleaned.get('direction_other') or '').strip():
            self.add_error('direction_other', 'Выберите направление или впишите специализацию.')
        return cleaned

from django import forms
from django.utils import timezone

from apps.interns.models import Position
from apps.teams import services
from apps.teams.models import TeamMember, TeamRole


# Роль в команде → направление человека (обратная сторона)
SPECIALIZATION_BY_ROLE = {
    TeamRole.BACKEND: 'Backend',
    TeamRole.FRONTEND: 'Frontend',
    TeamRole.UXUI: 'UX/UI',
    TeamRole.MOBILE: 'Mobile',
    TeamRole.QA: 'Testing/QA',
    TeamRole.PROJECT_MANAGER: 'PM',
}

# Направление человека → его роль в команде
ROLE_BY_SPECIALIZATION = {
    'Backend': TeamRole.BACKEND,
    'Frontend': TeamRole.FRONTEND,
    'UX/UI': TeamRole.UXUI,
    'Mobile': TeamRole.MOBILE,
    'Testing/QA': TeamRole.QA,
    'PM': TeamRole.PROJECT_MANAGER,
    'DevOps': TeamRole.OTHER,
}


class TeamMemberForm(forms.ModelForm):
    """Добавление участника в команду.

    Роль не выбирается — она берётся из направления человека
    (Backend, Frontend, UX/UI…). Отдельной галочкой отмечается тимлид.
    При суммарной загрузке > 100% показывается предупреждение (ТЗ §11).
    """

    is_lead = forms.BooleanField(
        label='Тимлид направления', required=False,
        help_text='Отметьте, если человек ведёт направление в этой команде',
    )

    class Meta:
        model = TeamMember
        fields = ['intern', 'comment']
        labels = {'intern': 'Участник'}
        widgets = {
            'comment': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, role=None, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.role = role
        # Проект нужен правилам состава (apps.teams.rules)
        self.project = project or (self.instance.project if self.instance.project_id else None)
        self.rule_warnings = []
        field = self.fields['intern']
        field.required = True
        field.queryset = (
            field.queryset.filter(is_archived=False)
            .select_related('specialization').order_by('full_name')
        )
        field.empty_label = 'Выберите человека'

        # Добавляют в конкретное направление — показываем только его людей
        spec_name = SPECIALIZATION_BY_ROLE.get(role)
        if spec_name:
            field.queryset = field.queryset.filter(
                specialization__name=spec_name,
            )
            field.help_text = f'Показаны только: {spec_name}'
        elif role == TeamRole.TEAM_LEAD:
            from apps.teams.selectors import lead_intern_ids

            field.queryset = field.queryset.filter(pk__in=lead_intern_ids())
            field.help_text = 'Показаны только те, кто уже тимлид — на другой проект'

        if role == TeamRole.TEAM_LEAD:
            self.fields['is_lead'].initial = True

        if self.instance.pk:
            self.fields['is_lead'].initial = (
                self.instance.role == TeamRole.TEAM_LEAD
            )

    def target_role(self, intern):
        role = getattr(self, 'role', None)
        if self.cleaned_data.get('is_lead') or role == TeamRole.TEAM_LEAD:
            return TeamRole.TEAM_LEAD
        if role:
            return role
        if intern and intern.position == Position.TEAM_LEAD:
            # Должность человека сильнее направления: тимлид остаётся
            # тимлидом и на новом проекте.
            return TeamRole.TEAM_LEAD
        spec = intern.specialization if intern else None
        return ROLE_BY_SPECIALIZATION.get(spec.name if spec else '', TeamRole.OTHER)

    def clean(self):
        cleaned = super().clean()
        intern = cleaned.get('intern')
        if intern and self.project is not None:
            from apps.teams.rules import validate_assignment

            status = cleaned.get('status', self.instance.status or TeamMember.Status.ACTIVE)
            self.rule_warnings = validate_assignment(
                intern, self.project, self.target_role(intern), exclude_member_pk=self.instance.pk,
                joining=status == TeamMember.Status.ACTIVE,
            )
        return cleaned

    def save(self, commit=True):
        member = super().save(commit=False)
        member.role = self.target_role(member.intern)
        if not member.joined_at:
            member.joined_at = timezone.localdate()
        if commit:
            member.save()
        return member

    class Media:
        pass

    def overload_warning(self) -> str | None:
        """Сколько ещё проектов у человека — предупреждение при распылении."""
        if not self.is_valid():
            return None
        intern = self.cleaned_data.get('intern')
        if not intern:
            return None
        others = TeamMember.objects.filter(
            intern=intern, status=TeamMember.Status.ACTIVE,
        ).exclude(pk=self.instance.pk).count()
        if others >= 2:
            return (
                f'{intern.full_name} уже занят(а) на {others} проектах — '
                f'этот будет третьим.'
            )
        return None


class InternProjectAddForm(forms.ModelForm):
    """Добавление проекта прямо с карточки стажёра — обратная сторона
    TeamMemberForm: там из проекта выбирают человека, здесь наоборот,
    стажёр уже известен (фиксируется во view), выбирается только проект."""

    class Meta:
        model = TeamMember
        fields = ['project', 'comment']
        widgets = {
            'comment': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['project'].required = True
        self.fields['project'].queryset = (
            self.fields['project'].queryset.active().order_by('name')
        )
        self.fields['project'].empty_label = 'Выберите проект'

    def clean(self):
        cleaned = super().clean()
        project, intern = cleaned.get('project'), self.instance.intern
        if project and intern:
            from apps.teams.rules import validate_assignment

            spec = intern.specialization
            role = TeamRole.TEAM_LEAD if intern.position == Position.TEAM_LEAD else ROLE_BY_SPECIALIZATION.get(
                spec.name if spec else '', TeamRole.OTHER)
            validate_assignment(intern, project, role)
        return cleaned


class TeamMemberEditForm(TeamMemberForm):
    """Редактирование участника: добавляются статус и даты участия.

    В списке — только люди подходящего направления: если меняем PM,
    предлагаются PM, если бэкендера — бэкендеры.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        member = self.instance
        if not member.pk:
            return
        # Направление, из которого выбираем замену
        spec_by_role = {
            value: key for key, value in ROLE_BY_SPECIALIZATION.items()
        }
        spec_name = spec_by_role.get(member.role)
        if member.role == TeamRole.TEAM_LEAD and member.intern_id:
            # Тимлида меняем на человека того же направления
            spec = member.intern.specialization
            spec_name = spec.name if spec else None
        if spec_name:
            queryset = self.fields['intern'].queryset.filter(
                specialization__name=spec_name,
            )
            # Текущий участник всегда остаётся в списке
            if member.intern_id:
                queryset = queryset | self.fields['intern'].queryset.filter(
                    pk=member.intern_id,
                )
            self.fields['intern'].queryset = queryset.distinct()
            self.fields['intern'].help_text = f'Показаны только: {spec_name}'

    class Meta(TeamMemberForm.Meta):
        fields = [
            'intern', 'joined_at', 'left_at', 'status', 'comment',
        ]
        widgets = {
            'joined_at': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'left_at': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'comment': forms.Textarea(attrs={'rows': 2}),
        }

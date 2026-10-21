from django import forms

from apps.clients.forms import ClientForm
from apps.documents.forms import DocumentForm
from apps.projects.models import Project


class PMClientForm(ClientForm):
    """Та же карточка клиента, но без реквизитов и внутренних комментариев —
    это поля для сотрудников, ПМ их не видит и не трогает."""

    class Meta(ClientForm.Meta):
        fields = [
            field for field in ClientForm.Meta.fields
            if field not in ('requisites', 'comment')
        ]


class PMDocumentForm(DocumentForm):
    """Та же форма документа, но проект зафиксирован своим."""

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['project'].queryset = Project.objects.filter(pk=project.pk)
        self.fields['project'].initial = project
        self.fields['project'].disabled = True


class PMProjectTypeForm(forms.ModelForm):
    """Тип проекта указывает ПМ: от него зависит набор этапов и ролей."""

    class Meta:
        model = Project
        fields = ['project_type']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['project_type'].required = False
        self.fields['project_type'].empty_label = 'Тип пока не выбран'

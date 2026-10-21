import os

from django import forms
from django.conf import settings

from apps.documents.models import Document, DocumentTemplate, ProjectBrief


def validate_upload_file(file):
    """Проверка расширения и размера файла (ТЗ §35) — общая для всех
    форм загрузки документов."""
    if not file or not hasattr(file, 'size'):
        return file
    extension = os.path.splitext(file.name)[1].lstrip('.').lower()
    allowed = settings.ALLOWED_UPLOAD_EXTENSIONS
    if extension not in allowed:
        raise forms.ValidationError(
            f'Файлы .{extension} запрещены. Разрешены: {", ".join(allowed)}.',
        )
    if file.size > settings.FILE_UPLOAD_MAX_MEMORY_SIZE:
        raise forms.ValidationError('Файл больше 20 МБ.')
    return file


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = [
            'project', 'doc_type', 'number', 'file', 'document_date',
            'status', 'is_signed', 'signed_date', 'comment',
        ]
        widgets = {
            'document_date': forms.DateInput(
                attrs={'type': 'date'}, format='%Y-%m-%d',
            ),
            'signed_date': forms.DateInput(
                attrs={'type': 'date'}, format='%Y-%m-%d',
            ),
            'comment': forms.Textarea(attrs={'rows': 2}),
        }

    def clean_file(self):
        return validate_upload_file(self.cleaned_data.get('file'))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('is_signed') and not cleaned.get('signed_date'):
            self.add_error('signed_date', 'Укажите дату подписания.')
        return cleaned


class DocumentTemplateForm(forms.ModelForm):
    class Meta:
        model = DocumentTemplate
        fields = ['doc_type', 'name', 'file', 'comment']
        widgets = {
            'comment': forms.Textarea(attrs={'rows': 2}),
        }

    def clean_file(self):
        return validate_upload_file(self.cleaned_data.get('file'))


class ProjectBriefApplyForm(forms.ModelForm):
    """Публичный бриф проекта — без входа в систему.

    Контакты (организация/ФИО/телефон/email) — не поля ``ProjectBrief``,
    это данные карточки клиента проекта; сюда добавлены отдельно и
    сохраняются в ``Client`` уже во view/сервисе.
    """

    organization = forms.CharField(label='Название компании', max_length=255)
    contact_name = forms.CharField(label='ФИО контактного лица', max_length=255)
    phone = forms.CharField(label='Телефон', max_length=32)
    email = forms.EmailField(label='Email')

    REQUIRED_FIELDS = [
        'about_business', 'goal', 'target_audience', 'required_features',
        'references', 'deadline_wish', 'existing_site_url', 'domain',
        'integrations', 'languages', 'content_owner', 'social_links',
    ]

    class Meta:
        model = ProjectBrief
        fields = [
            'about_business', 'goal', 'target_audience', 'required_features',
            'references', 'deadline_wish', 'existing_site_url', 'domain',
            'integrations', 'languages', 'content_owner', 'social_links',
            'competitors', 'brand_materials', 'decision_maker',
            'requirements_file', 'preferred_contact', 'additional_notes',
        ]
        widgets = {
            'about_business': forms.Textarea(attrs={'rows': 3}),
            'goal': forms.Textarea(attrs={'rows': 3}),
            'target_audience': forms.Textarea(attrs={'rows': 2}),
            'required_features': forms.Textarea(attrs={'rows': 4}),
            'references': forms.Textarea(attrs={'rows': 2}),
            'integrations': forms.Textarea(attrs={'rows': 2}),
            'social_links': forms.Textarea(attrs={'rows': 2}),
            'competitors': forms.Textarea(attrs={'rows': 2}),
            'additional_notes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        if client is not None:
            self.fields['organization'].initial = client.organization
            self.fields['contact_name'].initial = client.contact_name
            self.fields['phone'].initial = client.phone
            self.fields['email'].initial = client.email
        for name in self.REQUIRED_FIELDS:
            self.fields[name].required = True

    def clean_requirements_file(self):
        return validate_upload_file(self.cleaned_data.get('requirements_file'))


class NewProjectBriefForm(ProjectBriefApplyForm):
    """Бриф на новый проект — по общей ссылке, без привязки к проекту.

    Проекта у нас ещё нет, поэтому контакты и название сохраняем прямо в
    брифе: это заявка, из которой руководитель заводит проект.
    """

    project_name = forms.CharField(label='Название проекта', max_length=255)
    # Ловушка для ботов: поле скрыто от людей, заполненное — признак спама
    website = forms.CharField(required=False, label='Сайт компании (не заполняйте)')

    class Meta(ProjectBriefApplyForm.Meta):
        fields = [
            'project_name', 'organization', 'contact_name', 'phone', 'email',
            *ProjectBriefApplyForm.Meta.fields,
        ]

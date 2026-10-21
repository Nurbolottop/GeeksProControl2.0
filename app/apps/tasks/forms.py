import os

from django import forms
from django.conf import settings

from apps.tasks.models import Task, TaskAttachment, TaskComment


class TaskForm(forms.ModelForm):
    """Форма задачи: только то, что реально заполняют.

    Этап, исполнитель и статус здесь не нужны — статус меняется кнопками
    в списке и карточке задачи.
    """

    class Meta:
        model = Task
        fields = ['title', 'description', 'project', 'priority', 'deadline']
        widgets = {
            'title': forms.TextInput(
                attrs={'placeholder': 'Что нужно сделать', 'autofocus': True},
            ),
            'description': forms.Textarea(
                attrs={'rows': 4, 'placeholder': 'Детали, ссылки, требования — по желанию'},
            ),
            'deadline': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Задача может быть личной — без проекта
        field = self.fields['project']
        field.required = False
        field.empty_label = 'Без проекта — личная задача'


class TaskCommentForm(forms.ModelForm):
    class Meta:
        model = TaskComment
        fields = ['text']
        widgets = {
            'text': forms.Textarea(
                attrs={'rows': 2, 'placeholder': 'Комментарий…'},
            ),
        }


class TaskAttachmentForm(forms.ModelForm):
    class Meta:
        model = TaskAttachment
        fields = ['file']

    def clean_file(self):
        """Проверка расширения и размера файла (ТЗ §35)."""
        file = self.cleaned_data['file']
        extension = os.path.splitext(file.name)[1].lstrip('.').lower()
        allowed = settings.ALLOWED_UPLOAD_EXTENSIONS
        if extension not in allowed:
            raise forms.ValidationError(
                f'Файлы .{extension} запрещены. Разрешены: {", ".join(allowed)}.',
            )
        if file.size > settings.FILE_UPLOAD_MAX_MEMORY_SIZE:
            raise forms.ValidationError('Файл больше 20 МБ.')
        return file

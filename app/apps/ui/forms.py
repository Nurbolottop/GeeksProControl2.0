"""Демо-форма для каталога /ui/: по одному полю каждого вида."""
from django import forms


class DemoForm(forms.Form):
    name = forms.CharField(label='Название проекта', max_length=80, help_text='Как проект будет называться в списках.')
    email = forms.EmailField(label='Email заказчика')
    role = forms.ChoiceField(label='Роль в команде', choices=[
        ('', 'Выберите роль'), ('backend', 'Backend'), ('frontend', 'Frontend'), ('qa', 'QA'),
    ])
    deadline = forms.DateField(label='Дедлайн', widget=forms.DateInput(attrs={'type': 'date'}))
    priority = forms.ChoiceField(label='Приоритет', widget=forms.RadioSelect, initial='normal', choices=[
        ('low', 'Низкий'), ('normal', 'Обычный'), ('high', 'Высокий'),
    ])
    stages = forms.MultipleChoiceField(label='Этапы', required=False, widget=forms.CheckboxSelectMultiple, choices=[
        ('design', 'Дизайн'), ('dev', 'Разработка'), ('test', 'Тестирование'),
    ])
    comment = forms.CharField(label='Комментарий', required=False, widget=forms.Textarea(attrs={'rows': 3}))
    attachment = forms.FileField(label='Вложение', required=False)
    notify = forms.BooleanField(label='Уведомить команду', required=False)

    def clean_name(self):
        name = self.cleaned_data['name']
        if len(name) < 3:
            raise forms.ValidationError('Название слишком короткое — минимум 3 символа.')
        return name

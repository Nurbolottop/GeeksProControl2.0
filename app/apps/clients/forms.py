from django import forms

from apps.clients.models import Client


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = [
            'organization', 'contact_name', 'phone', 'email',
            'address', 'city', 'requisites', 'comment',
        ]
        widgets = {
            'requisites': forms.Textarea(attrs={'rows': 3}),
            'comment': forms.Textarea(attrs={'rows': 2}),
        }

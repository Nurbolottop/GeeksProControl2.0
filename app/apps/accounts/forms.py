from django.contrib.auth.forms import AuthenticationForm
from django import forms


class PhoneAuthenticationForm(AuthenticationForm):
    """Тот же логин по username, просто подписан как «Телефон» — у ПМ
    логином служит номер телефона."""

    username = forms.CharField(label='Телефон')

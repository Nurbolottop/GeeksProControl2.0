"""Вход по телефону в любом формате.

Логины заводились по-разному: у кого-то «+996700123456», у кого-то
«0770654321». Человек помнит свой номер, а не то, как его записали в
базе, — и получал «неверный логин или пароль», хотя пароль верный.
Поэтому сравниваем не строки, а последние девять цифр номера: +996,
996, 0, пробелы, скобки и дефисы значения не имеют.
"""
import re

from django.contrib.auth.backends import ModelBackend

from apps.accounts.models import User

# Девять цифр — это номер без кода страны: по ним человек и узнаётся.
PHONE_TAIL = 9


def _digits(value: str) -> str:
    return re.sub(r'\D', '', value or '')


class PhoneBackend(ModelBackend):
    """Сначала обычный вход по логину, затем — поиск по номеру."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        user = super().authenticate(
            request, username=username, password=password, **kwargs,
        )
        if user is not None:
            return user
        if not username or not password:
            return None

        tail = _digits(username)[-PHONE_TAIL:]
        if len(tail) < PHONE_TAIL:
            return None
        for candidate in User.objects.filter(is_active=True):
            if _digits(candidate.username)[-PHONE_TAIL:] != tail:
                continue
            if candidate.check_password(password) and self.user_can_authenticate(candidate):
                return candidate
        return None

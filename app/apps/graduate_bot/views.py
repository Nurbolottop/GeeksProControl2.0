from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


@login_required
def activity_list(request):
    """11F: отдельная страница не нужна — бот один на всех, связь с Telegram видна
    в карточках и на странице «Telegram-бот», выбор выпускников — в «Выпускниках»."""
    return redirect('interns:graduates')

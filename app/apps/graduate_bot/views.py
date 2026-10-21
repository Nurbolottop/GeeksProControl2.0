from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.graduate_bot import services


@login_required
def activity_list(request):
    return render(request, 'graduate_bot/activity_list.html', {
        'people': services.bot_activity(),
        'title': 'Бот-выпускник',
    })

from django.utils.functional import SimpleLazyObject

from apps.notifications import services


def notifications(request):
    """Счётчик непрочитанных уведомлений в навигации.

    Ленивый: запрос в БД — только если шаблон к нему обратился, а не на
    каждом htmx-фрагменте.
    """
    if not request.user.is_authenticated:
        return {}
    return {'unread_notifications': SimpleLazyObject(services.unread_count)}

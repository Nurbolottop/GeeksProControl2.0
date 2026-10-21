"""NotificationService (ТЗ §46.1)."""
from apps.notifications.models import Notification, NotificationLevel


def notify(
    title: str, *, level: str = NotificationLevel.INFO,
    description: str = '', url: str = '', dedup_key: str = '',
    intern=None,
) -> Notification | None:
    """Создаёт уведомление. С dedup_key не дублирует незакрытые.

    ``intern`` — личное уведомление в портал этого человека; без него
    уведомление уходит в общую ленту руководителя.
    """
    if dedup_key and Notification.objects.filter(
        dedup_key=dedup_key, is_closed=False, intern=intern,
    ).exists():
        return None
    return Notification.objects.create(
        title=title, level=level, description=description,
        url=url, dedup_key=dedup_key, intern=intern,
    )


def feed():
    """Общая лента руководителя — без личных уведомлений порталов."""
    return Notification.objects.filter(intern__isnull=True)


def unread_count() -> int:
    return feed().filter(is_read=False, is_closed=False).count()


def personal(intern):
    """Открытые личные уведомления человека — для его портала."""
    if intern is None:
        return Notification.objects.none()
    return Notification.objects.filter(intern=intern, is_closed=False)

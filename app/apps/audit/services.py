"""Запись в журнал аудита (ТЗ §27)."""
from apps.audit.models import AuditLog


def log(
    obj, action: str, *, old_value: str = '', new_value: str = '',
    reason: str = '', user=None,
) -> AuditLog:
    return AuditLog.objects.create(
        object_type=type(obj).__name__,
        object_id=str(getattr(obj, 'pk', '') or ''),
        object_repr=str(obj)[:255],
        action=action,
        old_value=old_value,
        new_value=new_value,
        reason=reason,
        user=user,
    )

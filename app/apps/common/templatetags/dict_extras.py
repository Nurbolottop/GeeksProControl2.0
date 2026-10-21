from django import template

register = template.Library()


@register.filter
def dict_get(value, key):
    """Достаёт значение из словаря по ключу: {{ data|dict_get:'x' }}."""
    if isinstance(value, dict):
        return value.get(key)
    return None

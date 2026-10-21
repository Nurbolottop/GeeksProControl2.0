from django import template

from apps.pm_portal import services, stages

register = template.Library()


@register.inclusion_tag('pm_portal/partials/stage_alerts.html', takes_context=True)
def stage_alerts(context):
    """Красное напоминание сверить этапы — на каждой странице портала ПМ."""
    request = context.get('request')
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {'alerts': []}
    projects = services.pm_projects(user)
    return {
        'alerts': stages.stage_alerts(projects),
        'reminder_from': stages.REMINDER_FROM,
        'request': request,
        'csrf_token': context.get('csrf_token'),
    }

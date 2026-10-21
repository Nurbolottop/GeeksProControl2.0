"""Помощники для htmx-ответов в дизайн-системе 2.0."""
import json


def with_toast(response, message: str, level: str = 'success'):
    """Показать тост после htmx-запроса: каркас слушает событие gp:toast."""
    triggers = {}
    if response.has_header('HX-Trigger'):
        try:
            triggers = json.loads(response['HX-Trigger'])
        except ValueError:
            triggers = {response['HX-Trigger']: None}
    triggers['gp:toast'] = {'message': message, 'level': level}
    # Только ASCII: HTTP-заголовок с кириллицей Django закодирует в MIME, и htmx его не разберёт
    response['HX-Trigger'] = json.dumps(triggers)
    return response

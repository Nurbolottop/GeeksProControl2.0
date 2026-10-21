"""Четыре обязательных документа проекта (UI-03B).

Бриф заказчика, договор, ТЗ, подписанный акт приёма-передачи. Права:

* руководитель (полный доступ) — всё, в том числе исправление документов
  закрытого проекта (только с причиной, в журнал);
* назначенный ПМ и назначенный тимлид проекта (активное участие) — смотреть,
  загружать, заменять и удалять документы своего проекта, пока он не закрыт;
* тимлид, который вёл уже закрытый проект, — только смотреть;
* остальные — ничего (views отдают 404, файлы — apps.common.media).

Замена — новая версия: прежняя запись уходит в архив с причиной «заменён»,
файл не удаляется. Удаление — тоже архив («удалён»), файл остаётся для
истории. Каждое действие — запись в историю проекта и журнал аудита.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.documents.models import (
    ACCEPTANCE_ACT,
    BRIEF,
    CONTRACT,
    REQUIREMENTS,
    Document,
    DocumentStatus,
    DocumentType,
)
from apps.projects.models import Project, ProjectStatus, ProjectStatusHistory

REQUIRED_DOCUMENTS = (
    (BRIEF, 'Бриф заказчика'),
    (CONTRACT, 'Договор'),
    (REQUIREMENTS, 'ТЗ'),
    (ACCEPTANCE_ACT, 'Подписанный акт приёма-передачи'),
)
LABELS = dict(REQUIRED_DOCUMENTS)
DEAD_STATUSES = (DocumentStatus.CANCELLED, DocumentStatus.EXPIRED)


@dataclass(frozen=True)
class Rights:
    view: bool = False
    edit: bool = False          # загрузить / заменить / удалить
    correction: bool = False    # правка закрытого проекта — только руководитель, с причиной


def _is_active_staff(user, project, role) -> bool:
    from apps.teams.models import TeamMember

    return TeamMember.objects.filter(
        project=project, role=role, status=TeamMember.Status.ACTIVE, intern__user=user,
    ).exists()


def rights(user, project: Project) -> Rights:
    from apps.accounts.access import has_full_access
    from apps.accounts.models import User
    from apps.teams.models import TeamRole

    if not getattr(user, 'is_authenticated', False):
        return Rights()
    closed = project.status == ProjectStatus.COMPLETED
    if has_full_access(user):
        return Rights(view=True, edit=not closed, correction=closed)
    role = getattr(user, 'role', '')
    if role == User.Role.PROJECT_MANAGER and _is_active_staff(user, project, TeamRole.PROJECT_MANAGER):
        return Rights(view=True, edit=not closed)
    if role == User.Role.TEAM_LEAD:
        if _is_active_staff(user, project, TeamRole.TEAM_LEAD):
            return Rights(view=True, edit=not closed)
        from apps.lead_portal.services import lead_projects

        if lead_projects(user).filter(pk=project.pk).exists():
            return Rights(view=True)  # вёл закрытый проект — только просмотр
    return Rights()


def doc_type(code: str) -> DocumentType:
    from apps.documents.services import ensure_default_types

    if code not in LABELS:
        raise ValidationError('Неизвестный тип документа.')
    ensure_default_types()
    return DocumentType.objects.get(code=code)


def current(project: Project, code: str) -> Document | None:
    """Действующая версия документа этого типа (последняя не убранная)."""
    return (
        project.documents.filter(is_archived=False, doc_type__code=code)
        .exclude(status__in=DEAD_STATUSES)
        .select_related('uploaded_by', 'doc_type').order_by('-created_at', '-pk').first()
    )


def versions(project: Project, code: str) -> list[Document]:
    return list(
        project.documents.filter(doc_type__code=code)
        .select_related('uploaded_by', 'removed_by').order_by('-created_at', '-pk'),
    )


def _log(project, text, user, reason=''):
    from apps.audit.services import log as audit_log

    ProjectStatusHistory.objects.create(project=project, field='Документы', new_value=text, reason=reason, user=user)
    audit_log(project, 'Документы', new_value=text, reason=reason, user=user)


def _check(user, project, reason):
    allowed = rights(user, project)
    if allowed.edit:
        return ''
    if allowed.correction:
        if not (reason or '').strip():
            raise ValidationError('Проект закрыт: укажите причину исправления — она попадёт в журнал.')
        return 'Исправление закрытого проекта'
    if allowed.view:
        raise PermissionDenied('Документы закрытого проекта меняет только руководитель.')
    raise PermissionDenied('Нет доступа к документам проекта.')


def _validate_file(file):
    from apps.documents.forms import validate_upload_file

    if not file:
        raise ValidationError('Выберите файл.')
    try:
        validate_upload_file(file)
    except Exception as error:  # forms.ValidationError → ValidationError с тем же текстом
        raise ValidationError(getattr(error, 'messages', [str(error)])) from error


def upload(project: Project, code: str, file, user, *, title: str = '', document_date=None,
           comment: str = '', reason: str = '') -> Document:
    """Загрузить документ; если такой уже есть — это замена (новая версия)."""
    marker = _check(user, project, reason)
    kind = doc_type(code)
    _validate_file(file)
    with transaction.atomic():
        Project.objects.select_for_update().filter(pk=project.pk).first()
        previous = current(project, code)
        now = timezone.now()
        document = Document(
            project=project, doc_type=kind, file=file, uploaded_by=user,
            title=(title or '').strip()[:200] or LABELS[code],
            original_name=(getattr(file, 'name', '') or '')[:255],
            document_date=document_date or timezone.localdate(), comment=comment or '',
        )
        if code == ACCEPTANCE_ACT:
            # Загружают именно подписанный акт
            document.status, document.is_signed = DocumentStatus.SIGNED, True
            document.signed_date = document.document_date
        document.save()
        if previous is not None:
            _remove(previous, 'replaced', user, now)
        version = project.documents.filter(doc_type=kind).count()
        action = f'Заменён: {LABELS[code]} (версия {version})' if previous else f'Загружен: {LABELS[code]}'
        _log(project, action, user, reason=' — '.join(filter(None, [marker, reason.strip()])))
    return document


def _remove(document, why, user, now):
    document.is_archived = True
    document.archived_at = now
    document.removed_reason = why
    document.removed_at = now
    document.removed_by = user
    document.save(update_fields=['is_archived', 'archived_at', 'removed_reason', 'removed_at', 'removed_by', 'updated_at'])


def delete(document: Document, user, *, reason: str = '') -> None:
    """Убрать действующий документ: запись и файл остаются в истории версий."""
    project = document.project
    marker = _check(user, project, reason)
    if document.is_archived:
        raise ValidationError('Документ уже убран.')
    with transaction.atomic():
        Project.objects.select_for_update().filter(pk=project.pk).first()
        _remove(document, 'deleted', user, timezone.now())
        label = LABELS.get(document.doc_type.code, str(document.doc_type))
        _log(project, f'Удалён: {label}', user, reason=' — '.join(filter(None, [marker, reason.strip()])))


def overview(project: Project, user) -> dict:
    """Данные вкладки «Документы»: четыре строки, права, история версий."""
    allowed = rights(user, project)
    rows = []
    for code, label in REQUIRED_DOCUMENTS:
        history = versions(project, code)
        active = next((d for d in history if not d.is_archived and d.status not in DEAD_STATUSES), None)
        rows.append({
            'code': code, 'label': label, 'document': active,
            'history': [d for d in history if d is not active],
            'is_brief': code == BRIEF,
        })
    present = sum(1 for row in rows if row['document'])
    return {
        'rows': rows, 'rights': allowed, 'present': present, 'total': len(rows),
        'missing': [row['label'] for row in rows if not row['document']],
        'closed': project.status == ProjectStatus.COMPLETED,
        'brief': getattr(project, 'brief', None),
        'present_label': f'{present} из {len(rows)}',
    }

"""Контроль комплекта документов проекта (ТЗ §17.1)."""
from django.db import models

from apps.documents.models import (
    BRIEF,
    DEFAULT_TYPES,
    Document,
    DocumentStatus,
    DocumentType,
    ProjectBrief,
    ProjectBriefLink,
)
from apps.projects.models import Project


def ensure_default_types() -> None:
    """Досоздаёт недостающие типы документов.

    Проверяется каждый тип по отдельности, поэтому новые типы
    (например, бриф) появляются и на уже работающей базе.
    """
    existing = set(DocumentType.objects.values_list('code', flat=True))
    missing = [
        DocumentType(code=code, name=name, required_for_delivery=required)
        for code, name, required in DEFAULT_TYPES
        if code not in existing
    ]
    if missing:
        DocumentType.objects.bulk_create(missing)


def document_progress(project: Project) -> dict:
    """Прогресс комплекта обязательных документов: «Документы: 2/3».

    Обязательный документ считается закрытым, если он загружен
    и не отменён/не истёк.
    """
    ensure_default_types()
    required_types = list(DocumentType.objects.filter(required_for_delivery=True))
    present_type_ids = set(
        project.documents.active()
        .exclude(status__in=[DocumentStatus.CANCELLED, DocumentStatus.EXPIRED])
        .values_list('doc_type_id', flat=True),
    )
    missing = [t for t in required_types if t.pk not in present_type_ids]
    return {
        'total': len(required_types),
        'done': len(required_types) - len(missing),
        'missing': missing,
    }


def has_signed_document(project: Project, type_code: str) -> bool:
    return project.documents.active().filter(
        doc_type__code=type_code, is_signed=True,
    ).exists()


def has_document(project: Project, type_code: str) -> bool:
    return (
        project.documents.active()
        .exclude(status__in=[DocumentStatus.CANCELLED, DocumentStatus.EXPIRED])
        .filter(doc_type__code=type_code)
        .exists()
    )


# --- Бриф по ссылке ---------------------------------------------------
# ПМ выпускает ссылку на конкретный проект и отправляет её заказчику сам
# (WhatsApp/Telegram/email — вручную). Заполнение обновляет одну и ту же
# карточку ProjectBrief проекта, а не плодит новые записи при повторной
# отправке.

def issue_brief_link(project: Project, user=None, ttl_days: int | None = 30) -> ProjectBriefLink:
    """Новая ссылка на бриф проекта, гася прежние активные ссылки этого
    же проекта — чтобы старая, отправленная по ошибке, не гуляла дальше."""
    from datetime import timedelta

    from django.utils import timezone

    ProjectBriefLink.objects.filter(project=project, is_active=True).update(
        is_active=False,
    )
    return ProjectBriefLink.objects.create(
        project=project,
        created_by=user if user and user.is_authenticated else None,
        expires_at=timezone.now() + timedelta(days=ttl_days) if ttl_days else None,
    )


def active_brief_link(project: Project) -> ProjectBriefLink | None:
    """Действующая ссылка на бриф проекта или None."""
    from django.utils import timezone

    link = (
        ProjectBriefLink.objects.filter(project=project, is_active=True)
        .order_by('-created_at').first()
    )
    if link is not None and link.is_expired:
        link.is_active = False
        link.save(update_fields=['is_active', 'updated_at'])
        return None
    return link


DUPLICATE_WINDOW_HOURS = 24


def _digits(phone: str) -> str:
    return ''.join(ch for ch in phone or '' if ch.isdigit())


def find_duplicate_brief(project_name: str, phone: str):
    """Тот же заказчик (телефон) с тем же проектом за последние сутки."""
    from datetime import timedelta

    from django.utils import timezone

    since = timezone.now() - timedelta(hours=DUPLICATE_WINDOW_HOURS)
    digits = _digits(phone)
    for brief in ProjectBrief.objects.filter(
        submitted_at__gte=since, project_name__iexact=(project_name or '').strip(),
    ).select_related('project'):
        if digits and _digits(brief.phone) == digits:
            return brief
    return None


def accept_new_project_brief(form) -> tuple[ProjectBrief, bool]:
    """Бриф на новый проект (общая ссылка) — проект заводится сразу (UI-03B).

    Бриф сохраняется и привязывается к новому проекту (с карточкой клиента),
    руководителю — уведомление со ссылкой на проект. Повторная отправка того
    же брифа (тот же телефон и проект за сутки) нового проекта не заводит.
    Возвращает (бриф, создан ли новый).
    """
    from django.db import transaction
    from django.utils import timezone

    from apps.notifications.models import NotificationLevel
    from apps.notifications.services import notify

    data = form.cleaned_data
    duplicate = find_duplicate_brief(data.get('project_name', ''), data.get('phone', ''))
    if duplicate is not None:
        return duplicate, False
    with transaction.atomic():
        brief = form.save(commit=False)
        brief.project = None
        brief.submitted_at = timezone.now()
        brief.save()
        project = project_from_brief(brief)
    notify(
        f'Новый проект с брифа: {project.name}',
        level=NotificationLevel.INFO,
        description=(
            f'{brief.organization} ({brief.contact_name}, {brief.phone}) заполнил бриф — '
            'проект заведён автоматически, бриф уже в документах.'
        ),
        url=f'{project.get_absolute_url()}?tab=documents',
        dedup_key=f'brief-project:{project.pk}',
    )
    return brief, True


def attach_brief_document(project, brief) -> Document:
    """Бриф из формы — действующий документ «Бриф заказчика» (без файла)."""
    from django.utils import timezone

    from apps.documents.project_documents import current

    ensure_default_types()
    document = current(project, BRIEF)
    if document is None:
        document = Document.objects.create(
            project=project, doc_type=DocumentType.objects.get(code=BRIEF),
            status=DocumentStatus.DRAFT, title='Бриф заказчика (форма)',
            document_date=(brief.submitted_at or timezone.now()).date(),
        )
    return document


def project_from_brief(brief: ProjectBrief, user=None):
    """Завести проект из брифа: клиент, проект и сам бриф привязываются.

    Повторный вызов ничего не плодит — если проект уже заведён, просто
    возвращаем его.
    """
    from django.db import transaction

    from apps.clients.models import Client
    from apps.projects.models import Project
    from apps.projects.services import create_project

    with transaction.atomic():
        brief = ProjectBrief.objects.select_for_update().get(pk=brief.pk)
        if brief.project_id:
            return brief.project
        client = Client.objects.filter(organization__iexact=brief.organization.strip()).first()
        if client is None:
            client = Client.objects.create(
                organization=brief.organization.strip(), contact_name=brief.contact_name,
                phone=brief.phone, email=brief.email,
            )
        project = create_project(Project(name=brief.project_name.strip() or brief.organization, client=client))
        brief.project = project
        brief.save(update_fields=['project', 'updated_at'])
        attach_brief_document(project, brief)
    return project


def accept_brief_submission(link: ProjectBriefLink, form) -> ProjectBrief:
    """Заказчик отправил бриф по ссылке.

    Контакты уходят в карточку клиента проекта (создаётся, если её ещё
    не было), остальные ответы — в ProjectBrief. Плюс автоматически
    заводится/обновляется Document(doc_type=brief, status=DRAFT) —
    ПМ увидит его в чек-листе «Документы» и утвердит после проверки,
    как любой другой загруженный документ.
    """
    from django.utils import timezone

    from apps.clients.models import Client

    ensure_default_types()
    now = timezone.now()
    project = link.project
    data = form.cleaned_data

    client = project.client or Client()
    client.organization = data['organization']
    client.contact_name = data['contact_name']
    client.phone = data['phone']
    client.email = data['email']
    client.save()
    if project.client_id != client.pk:
        project.client = client
        project.save(update_fields=['client', 'updated_at'])

    brief = form.save(commit=False)
    brief.project = project
    brief.submitted_at = now
    brief.save()

    attach_brief_document(project, brief)

    ProjectBriefLink.objects.filter(pk=link.pk).update(
        submissions=models.F('submissions') + 1, used_at=now,
    )
    return brief

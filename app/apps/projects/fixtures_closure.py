"""Подготовка проекта к закрытию — для тестов (UI-03B).

Три подтверждения руководителя и четыре обязательных документа, как если
бы их загрузили через вкладку «Документы».
"""
from django.core.files.uploadedfile import SimpleUploadedFile


def head(username='closure-head'):
    from django.contrib.auth import get_user_model

    from apps.accounts.models import User

    model = get_user_model()
    return model.objects.filter(username=username).first() or model.objects.create_user(
        username=username, password='x', role=User.Role.HEAD,
    )


def pdf(name='doc.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 test', content_type='application/pdf')


def upload_required(project, user):
    from apps.documents import project_documents as docs

    for code, _label in docs.REQUIRED_DOCUMENTS:
        docs.upload(project, code, pdf(f'{code}.pdf'), user)


def confirm_all(project, user):
    from apps.projects import closure

    for key in closure.CONFIRMATIONS:
        closure.set_confirmation(project, key, True, user)
    project.refresh_from_db()


def make_ready(project, user=None):
    user = user or head()
    upload_required(project, user)
    confirm_all(project, user)
    return user

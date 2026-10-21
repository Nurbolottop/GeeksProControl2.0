from django.urls import path

from apps.documents import project_views as document_views
from apps.pm_portal import views
from apps.reports import portal_views as report_views

app_name = 'pm_portal'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('reports/', report_views.pm_reports, name='weekly_reports'),
    path('reports/submit/', report_views.pm_report_submit, name='weekly_report_submit'),
    path('<int:pk>/', views.project_detail, name='project_detail'),
    path('<int:pk>/stages/<int:stage_pk>/', views.stage_set, name='stage_set'),
    path('<int:pk>/stages/confirm/', views.stages_confirm, name='stages_confirm'),
    path('<int:pk>/report/', views.report_create, name='report_create'),
    path(
        '<int:pk>/report/<int:report_pk>/edit/', views.report_update,
        name='report_update',
    ),
    path(
        '<int:pk>/report/<int:report_pk>/delete/', views.report_delete,
        name='report_delete',
    ),
    path('<int:pk>/team/add/', views.member_add, name='member_add'),
    path(
        '<int:pk>/team/<int:member_pk>/edit/', views.member_edit,
        name='member_edit',
    ),
    path(
        '<int:pk>/team/<int:member_pk>/delete/', views.member_delete,
        name='member_delete',
    ),
    path(
        '<int:pk>/attendance/<int:meeting_pk>/', views.meeting_detail,
        name='meeting_detail',
    ),
    path(
        '<int:pk>/interns/<int:intern_pk>/', views.intern_detail,
        name='intern_detail',
    ),
    # Документы проекта (UI-03B): загрузка/замена новой версией и удаление в архив
    path('<int:pk>/documents/upload/', document_views.document_upload, {'scope': 'pm'}, name='document_upload'),
    path('<int:pk>/documents/<int:document_pk>/delete/', document_views.document_delete, {'scope': 'pm'},
         name='document_delete'),
    path('<int:pk>/documents/brief-link/', document_views.brief_link_create, {'scope': 'pm'},
         name='brief_link_create'),
    path('<int:pk>/client/', views.client_edit, name='client_edit'),
    path('<int:pk>/edit/<str:section>/', views.project_edit, name='project_edit'),
    path('<int:pk>/primary-stage/', views.primary_stage_set, name='primary_stage_set'),
]

# Видеособрания (этап 9)
from apps.video.views import portal_urlpatterns as meeting_urls  # noqa: E402

urlpatterns += meeting_urls('pm')

# Задачи проекта (UI-03C) — общие views, зона роли 'pm'
from apps.tasks.project_views import urlpatterns_for  # noqa: E402

urlpatterns += urlpatterns_for('pm')

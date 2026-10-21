from django.urls import path

from apps.documents import project_views as document_views
from apps.projects import views

app_name = 'projects'

urlpatterns = [
    path('', views.project_list, name='list'),
    path('in-progress/', views.project_list,
         {'category': 'in_progress'}, name='list_in_progress'),
    path('rejected/', views.project_list,
         {'category': 'rejected'}, name='list_rejected'),
    path('completed/', views.project_list,
         {'category': 'completed'}, name='list_completed'),
    path('kanban/', views.project_kanban, name='kanban'),
    path('create/', views.project_create, name='create'),
    path('<int:pk>/', views.project_detail, name='detail'),
    path('<int:pk>/edit/', views.project_update, name='update'),
    path('<int:pk>/delete/', views.project_delete, name='delete'),
    path('<int:pk>/move-stage/', views.project_move_stage, name='move_stage'),
    path('<int:pk>/complete/', views.project_complete, name='complete'),
    path('stages/<int:pk>/', views.stage_update, name='stage_update'),
    path('stages/<int:pk>/row/', views.stage_row, name='stage_row'),
    path('stages/<int:pk>/complete/', views.stage_complete, name='stage_complete'),
    path('stages/<int:pk>/extend/', views.stage_extend, name='stage_extend'),
    path('<int:pk>/section/<str:section>/', views.project_section, name='section'),
    path('reports/', views.report_list, name='reports'),
    path('<int:pk>/reports/add/', views.report_create, name='report_create'),
    path('reports/<int:pk>/edit/', views.report_update, name='report_update'),
    path('reports/<int:pk>/delete/', views.report_delete, name='report_delete'),
    path('<int:pk>/access/add/', views.access_create, name='access_create'),
    path('access/<int:pk>/edit/', views.access_update, name='access_update'),
    path('access/<int:pk>/delete/', views.access_delete, name='access_delete'),
    path('<int:pk>/primary-stage/', views.primary_stage_set, name='primary_stage_set'),
    path('<int:pk>/recruitment/', views.recruitment_set, name='recruitment_set'),
    path('stages/<int:pk>/status/', views.stage_status_set, name='stage_status_set'),
    path('<int:pk>/closure/<str:key>/', views.closure_confirm, name='closure_confirm'),
    # Документы проекта (UI-03B) — те же views у ПМ и тимлида, в их зонах
    path('<int:pk>/documents/upload/', document_views.document_upload, {'scope': 'head'}, name='document_upload'),
    path('<int:pk>/documents/<int:document_pk>/delete/', document_views.document_delete, {'scope': 'head'},
         name='document_delete'),
    path('<int:pk>/documents/brief-link/', document_views.brief_link_create, {'scope': 'head'},
         name='brief_link_create'),
]

# Задачи проекта (UI-03C) — общие views, зона роли 'head'
from apps.tasks.project_views import urlpatterns_for  # noqa: E402

urlpatterns += urlpatterns_for('head')

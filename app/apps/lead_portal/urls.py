from django.urls import path

from apps.documents import project_views as document_views
from apps.lead_portal import views
from apps.reports import portal_views as report_views
from apps.tgbot import group_views

app_name = 'lead_portal'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('resume/', views.resume, name='resume'),
    path('reports/', report_views.lead_reports, name='weekly_reports'),
    path('telegram/', group_views.lead_groups, name='telegram_groups'),
    path('telegram/<int:pk>/<str:status>/', group_views.lead_mark, name='telegram_mark'),
    path('reports/<int:pk>/submit/', report_views.lead_report_submit, name='weekly_report_submit'),
    path('reports/<int:pk>/evaluate/', report_views.lead_evaluate, name='weekly_evaluate'),
    path(
        'notifications/<int:pk>/close/', views.notification_close,
        name='notification_close',
    ),
    path('<int:pk>/', views.project_detail, name='project_detail'),
    path(
        '<int:pk>/team/form-link/', views.profile_link_create,
        name='profile_link_create',
    ),
    path(
        '<int:pk>/team/form-link/disable/', views.profile_link_disable,
        name='profile_link_disable',
    ),
    path(
        '<int:pk>/team/<int:member_pk>/edit/', views.member_edit,
        name='member_edit',
    ),
    path(
        '<int:pk>/team/<int:member_pk>/delete/', views.member_delete,
        name='member_delete',
    ),
    path('<int:pk>/attendance/create/', views.meeting_create, name='meeting_create'),
    path(
        '<int:pk>/attendance/<int:meeting_pk>/', views.meeting_detail,
        name='meeting_detail',
    ),
    path(
        '<int:pk>/attendance/<int:meeting_pk>/mark/', views.meeting_mark_toggle,
        name='meeting_mark_toggle',
    ),
    path(
        '<int:pk>/attendance/<int:meeting_pk>/mark-all/', views.meeting_mark_all,
        name='meeting_mark_all',
    ),
    path(
        '<int:pk>/attendance/<int:meeting_pk>/score/', views.meeting_score,
        name='meeting_score',
    ),
    path(
        '<int:pk>/interns/<int:intern_pk>/', views.intern_detail,
        name='intern_detail',
    ),
    path('<int:pk>/interns/<int:intern_pk>/warn/', views.intern_warn, name='intern_warn'),
    path(
        '<int:pk>/interns/<int:intern_pk>/to-reserve/', views.intern_to_reserve,
        name='intern_to_reserve',
    ),
    path('<int:pk>/documents/upload/', document_views.document_upload, {'scope': 'lead'}, name='document_upload'),
    path('<int:pk>/documents/<int:document_pk>/delete/', document_views.document_delete, {'scope': 'lead'},
         name='document_delete'),
    path('<int:pk>/documents/brief-link/', document_views.brief_link_create, {'scope': 'lead'},
         name='brief_link_create'),
]

# Видеособрания (этап 9)
from apps.video.views import portal_urlpatterns as meeting_urls  # noqa: E402

urlpatterns += meeting_urls('lead')

# Задачи проекта (UI-03C) — общие views, зона роли 'lead'
from apps.tasks.project_views import urlpatterns_for  # noqa: E402

urlpatterns += urlpatterns_for('lead')

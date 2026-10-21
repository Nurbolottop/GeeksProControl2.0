from django.urls import path

from apps.documents import project_views, views

app_name = 'documents'

urlpatterns = [
    path('', views.document_list, name='list'),
    path('brief-requests/', views.brief_requests, name='brief_requests'),
    path(
        'brief-requests/<int:pk>/accept/', views.brief_request_accept,
        name='brief_request_accept',
    ),
    path('templates/', views.template_list, name='templates'),
    path('templates/create/', views.template_create, name='template_create'),
    path(
        'templates/<int:pk>/edit/', views.template_update,
        name='template_update',
    ),
    path(
        'templates/<int:pk>/delete/', views.template_delete,
        name='template_delete',
    ),
    path(
        'brief-link/<int:pk>/create/', project_views.brief_link_create, {'scope': 'head'},
        name='brief_link_create',
    ),
]

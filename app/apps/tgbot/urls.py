from django.urls import path

from apps.tgbot import group_views, views

app_name = 'tgbot'

urlpatterns = [
    path('', views.reconcile, name='reconcile'),
    path('link/<int:pk>/', views.link_decide, name='link_decide'),
    path('groups/code/', group_views.issue_code, name='issue_code'),
    path('groups/<int:pk>/<str:action>/', group_views.group_action, name='group_action'),
    path('access/<int:pk>/<str:status>/', group_views.head_mark, name='head_mark'),
]

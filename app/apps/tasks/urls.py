from django.urls import path

from apps.tasks import views

app_name = 'tasks'

urlpatterns = [
    path('', views.task_list, name='list'),
    path('kanban/', views.task_kanban, name='kanban'),
    path('create/', views.task_create, name='create'),
    path('<int:pk>/', views.task_detail, name='detail'),
    path('<int:pk>/edit/', views.task_update, name='update'),
    path('<int:pk>/status/', views.task_set_status, name='set_status'),
    path('<int:pk>/delete/', views.task_delete, name='delete'),
]

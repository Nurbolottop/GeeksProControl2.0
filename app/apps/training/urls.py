from django.urls import path

from apps.training import views

app_name = 'training'

urlpatterns = [
    path('', views.plan, name='plan'),
    path('groups/', views.group_list, name='group_list'),
    path('groups/create/', views.group_create, name='group_create'),
    path('groups/import/', views.group_import, name='group_import'),
    path('groups/<int:pk>/edit/', views.group_update, name='group_update'),
    path('groups/<int:pk>/delete/', views.group_delete, name='group_delete'),
]

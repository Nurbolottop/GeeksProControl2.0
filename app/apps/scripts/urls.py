from django.urls import path

from apps.scripts import views

app_name = 'scripts'

urlpatterns = [
    path('', views.script_list, name='list'),
    path('create/', views.script_create, name='create'),
    path('<int:pk>/edit/', views.script_update, name='update'),
    path('<int:pk>/delete/', views.script_delete, name='delete'),
    path('<int:pk>/pin/', views.script_pin, name='pin'),
]

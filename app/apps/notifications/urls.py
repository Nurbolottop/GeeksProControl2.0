from django.urls import path

from apps.notifications import views

app_name = 'notifications'

urlpatterns = [
    path('', views.notification_list, name='list'),
    path('<int:pk>/close/', views.notification_close, name='close'),
    path('close-all/', views.notification_close_all, name='close_all'),
]

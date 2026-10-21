from django.urls import path

from apps.reports import views

app_name = 'weekly_reports'

urlpatterns = [
    path('', views.board, name='board'),
]

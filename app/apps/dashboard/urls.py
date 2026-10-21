from django.urls import path

from apps.dashboard import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('dashboard/week/', views.week_activity, name='dashboard_week'),
    path('dashboard/compare/', views.compare, name='dashboard_compare'),
]

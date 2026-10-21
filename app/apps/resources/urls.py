from django.urls import path

from apps.resources import views

app_name = 'resources'

urlpatterns = [
    path('forecast/', views.forecast, name='forecast'),
    path('graduations/', views.graduations, name='graduations'),
    path('planned/create/', views.planned_create, name='planned_create'),
    path('planned/<int:pk>/', views.planned_update, name='planned_update'),
    path('requests/', views.staffing_requests, name='staffing_requests'),
    path(
        'requests/<int:pk>/toggle/', views.staffing_request_toggle,
        name='staffing_request_toggle',
    ),
    path(
        'requests/<int:pk>/delete/', views.staffing_request_delete,
        name='staffing_request_delete',
    ),
]

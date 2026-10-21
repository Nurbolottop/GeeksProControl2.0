from django.urls import path

from apps.attendance import views

app_name = 'attendance'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('groups/<int:pk>/', views.group_meetings, name='group_meetings'),
    path('groups/<int:pk>/sheet/', views.group_sheet, name='group_sheet'),
    path('meetings/<int:pk>/', views.meeting_detail, name='meeting_detail'),
    path('meetings/<int:pk>/mark/', views.mark_person, name='mark_person'),
    path('meetings/<int:pk>/score/', views.score_person, name='score_person'),
    path('groups/<int:pk>/meetings/create/', views.meeting_create,
         name='meeting_create'),
    path('meetings/<int:pk>/delete/', views.meeting_delete, name='meeting_delete'),
    path('meetings/<int:pk>/toggle/', views.toggle, name='toggle'),
    path('meetings/<int:pk>/mark-all/', views.meeting_mark_all, name='mark_all'),
]

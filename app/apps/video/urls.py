from django.urls import path

from apps.video import events, views

app_name = 'video'

urlpatterns = [
    path('', views.meeting_list, {'scope': 'head'}, name='list'),
    path('<int:pk>/', views.meeting_detail, {'scope': 'head'}, name='detail'),
    path('<int:pk>/do/<str:action>/', views.meeting_action, {'scope': 'head'}, name='action'),
    path('<int:pk>/participant/<int:participant_pk>/correct/', views.meeting_correct, {'scope': 'head'},
         name='correct'),
    # Публичные (без входа): персональная ссылка участника
    path('join/<str:token>/', views.public_join, name='join'),
    path('enter/<str:code>/', views.public_enter, name='enter'),
    # Вебхук видеосервиса: только с верной подписью (apps.video.events)
    path('webhook/<str:provider>/', events.webhook, name='webhook'),
]

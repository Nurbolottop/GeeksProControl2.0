from django.urls import path

from apps.graduate_bot import views

app_name = 'graduate_bot'

urlpatterns = [
    path('', views.activity_list, name='activity'),
]

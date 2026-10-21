from django.urls import path

from apps.flows import views

app_name = 'flows'

urlpatterns = [
    path('', views.flow_list, name='list'),
    path('create/', views.flow_create, name='create'),
    path('<int:pk>/', views.flow_detail, name='detail'),
    path('<int:pk>/edit/', views.flow_update, name='update'),
    path('<int:flow_pk>/groups/create/', views.group_create, name='group_create'),
    path('groups/<int:pk>/', views.group_detail, name='group_detail'),
    path('groups/<int:pk>/edit/', views.group_update, name='group_update'),
]

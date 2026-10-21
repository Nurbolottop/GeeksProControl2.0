from django.urls import path

from apps.risks import views

app_name = 'risks'

urlpatterns = [
    path('', views.risk_list, name='list'),
    path('create/', views.risk_create, name='create'),
    path('<int:pk>/close/', views.risk_close, name='close'),
]

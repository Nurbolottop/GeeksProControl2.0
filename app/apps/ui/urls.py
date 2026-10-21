from django.urls import path

from apps.ui import views

app_name = 'ui'

urlpatterns = [
    path('', views.catalog, name='catalog'),
    path('toast/', views.catalog_toast, name='catalog_toast'),
]

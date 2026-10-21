from django.urls import path

from apps.teams import staff_views, views

app_name = 'teams'

urlpatterns = [
    path('', views.team_overview, name='overview'),
    path('project/<int:project_pk>/add/', views.member_add, name='member_add'),
    path('group/<int:group_pk>/add/', views.member_add_to_group,
         name='member_add_group'),
    path('member/<int:pk>/edit/', views.member_edit, name='member_edit'),
    path('member/<int:pk>/delete/', views.member_delete, name='member_delete'),
    path('project/<int:project_pk>/clear/', views.team_clear, name='team_clear'),
    # ПМ и тимлиды (этап 6A)
    path('leads/', staff_views.lead_list, name='lead_list'),
    path('leads/add/', staff_views.assign, {'role': 'lead'}, name='lead_add'),
    path('leads/<int:pk>/remove/', staff_views.remove, name='lead_remove'),
    path('pm/', staff_views.pm_list, name='pm_list'),
    path('pm/add/', staff_views.assign, {'role': 'pm'}, name='pm_add'),
    path('pm/<int:pk>/remove/', staff_views.remove, name='pm_remove'),
    path('assignment/<int:pk>/replace/', staff_views.replace, name='replace'),
    path('staff/<int:pk>/', staff_views.staff_detail, name='staff_detail'),
    path('staff/<int:pk>/dismiss/', staff_views.dismiss, name='dismiss'),
]

from django.urls import path

from apps.reserve import board_views, views

app_name = 'reserve'

urlpatterns = [
    path('', views.overview, name='overview'),
    # Кадровый резерв 2.0 (этап 6B)
    path('board/', board_views.board, name='board'),
    path('<int:pk>/hr/', board_views.hr_detail, name='hr_detail'),
    path('<int:pk>/employment/', board_views.confirm_employment, name='confirm_employment'),
    path('<int:pk>/unreserve/', board_views.unreserve, name='unreserve'),
    path('lead/<int:intern_pk>/', board_views.reserve_lead, name='reserve_lead'),
    path('candidates/', views.candidate_list, name='list'),
    path('create/', views.candidate_create, name='create'),
    path('reorder/', views.candidates_reorder, name='reorder'),
    path('from-intern/', views.candidate_from_intern, name='from_intern'),
    path('invite/', views.invite_create, name='invite_create'),
    path('invite/<int:pk>/disable/', views.invite_disable, name='invite_disable'),
    path('<int:pk>/', views.candidate_detail, name='detail'),
    path('<int:pk>/edit/', views.candidate_update, name='update'),
    path('<int:pk>/evaluate/', views.candidate_evaluate, name='evaluate'),
    path('<int:pk>/status/', views.candidate_status, name='status'),
    path('<int:pk>/edit-link/', views.invite_create, name='edit_link'),
    path('<int:pk>/share/', views.share_create, name='share_create'),
    path('share-collection/', views.share_collection_create, name='share_collection_create'),
    path('share/<int:pk>/disable/', views.share_disable, name='share_disable'),
    path('<int:pk>/recommend/', views.recommendation_create, name='recommend'),
    path(
        'recommendation/<int:pk>/status/', views.recommendation_status,
        name='recommendation_status',
    ),
]

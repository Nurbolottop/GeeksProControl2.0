from django.urls import path

from apps.interns import views

app_name = 'interns'

urlpatterns = [
    path('', views.intern_list, name='list'),
    path('create/', views.intern_create, name='create'),
    path('by-project/', views.by_project, name='by_project'),
    path('by-branch/', views.by_branch, name='by_branch'),
    path(
        'profile-link/new/', views.profile_link_create,
        name='profile_link_create',
    ),
    path(
        'profile-link/disable/', views.profile_link_disable,
        name='profile_link_disable',
    ),
    path(
        'profile-link/answers/', views.profile_link_answers,
        name='profile_link_answers',
    ),
    path('reserve/', views.reserve_list, name='reserve'),
    path('reserve/add/', views.reserve_create, name='reserve_create'),
    path('reserve/<int:pk>/edit/', views.reserve_update, name='reserve_update'),
    path('reserve/<int:pk>/delete/', views.reserve_delete, name='reserve_delete'),
    path(
        'reserve/<int:pk>/priority/', views.reserve_set_priority,
        name='reserve_set_priority',
    ),
    path('resume-bank/', views.resume_bank_list, name='resume_bank'),
    path(
        'resume-bank/<int:pk>/approve/', views.resume_bank_approve,
        name='resume_bank_approve',
    ),
    path(
        'resume-bank/<int:pk>/revise/', views.resume_bank_revise,
        name='resume_bank_revise',
    ),
    path('graduates/', views.graduates_list, name='graduates'),
    path('waiting/', views.waiting_list, name='waiting'),
    path('freeze-requests/', views.internship_requests, name='internship_requests'),
    path('freeze-requests/<int:pk>/<str:decision>/', views.internship_request_decide, name='internship_request_decide'),
    path('discipline/<int:pk>/<str:decision>/', views.discipline_decide, name='discipline_decide'),
    path('<int:pk>/warn/', views.intern_warn, name='warn'),
    path('<int:pk>/cancel/', views.intern_cancel, name='cancel'),
    path('<int:pk>/reenroll/', views.intern_reenroll, name='reenroll'),
    path('<int:pk>/leave-project/', views.intern_leave, name='leave_project'),
    path('<int:pk>/', views.intern_detail, name='detail'),
    path('<int:pk>/edit/', views.intern_update, name='update'),
    path('<int:pk>/evaluate/', views.evaluation_add, name='evaluate'),
    path('<int:pk>/delete/', views.intern_delete, name='delete'),
    path('<int:pk>/archive/', views.intern_archive, name='archive'),
    path('<int:pk>/unarchive/', views.intern_unarchive, name='unarchive'),
    path('<int:pk>/pause/', views.intern_pause, name='pause'),
    path('<int:pk>/unpause/', views.intern_unpause, name='unpause'),
    path('<int:pk>/grant-access/', views.grant_pm_access, name='grant_access'),
    path(
        '<int:pk>/grant-lead-access/', views.grant_lead_access,
        name='grant_lead_access',
    ),
    path('<int:pk>/promote-lead/', views.intern_promote_lead, name='promote_lead'),
    path('<int:pk>/demote-lead/', views.intern_demote_lead, name='demote_lead'),
    path('<int:pk>/project/add/', views.intern_project_add, name='project_add'),
    path(
        '<int:pk>/project/<int:member_pk>/remove/', views.intern_project_remove,
        name='project_remove',
    ),
]

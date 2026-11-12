from django.urls import path

from . import group_views as v

app_name = 'org-api'

urlpatterns = [
    path('groups/', v.GroupListCreateView.as_view(), name='group-list'),
    path('groups/<uuid:pk>/', v.GroupDetailView.as_view(), name='group-detail'),
    path('groups/<uuid:pk>/members/', v.MemberListCreateView.as_view(), name='member-list'),
    path('groups/<uuid:pk>/members/bulk/', v.MemberBulkView.as_view(), name='member-bulk'),
    path(
        'groups/<uuid:pk>/members/import-csv/',
        v.MemberCsvImportView.as_view(),
        name='member-import-csv',
    ),
    path(
        'groups/<uuid:pk>/members/<uuid:member_id>/',
        v.MemberDetailView.as_view(),
        name='member-detail',
    ),
    path('lessons/', v.LessonListCreateView.as_view(), name='lesson-list'),
    path('lessons/<uuid:pk>/', v.LessonDetailView.as_view(), name='lesson-detail'),
]

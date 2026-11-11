from django.urls import path

from . import content_plan_api

app_name = 'catalog-api'

urlpatterns = [
    path('content-plan/', content_plan_api.ContentPlanView.as_view(), name='content-plan'),
    path(
        'content-plan/subjects/<uuid:subject_id>/target/',
        content_plan_api.ContentTargetView.as_view(),
        name='content-plan-target',
    ),
]

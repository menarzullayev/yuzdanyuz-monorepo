"""Task 6 — Intelligence (AI Diagnostika) URLs."""

from django.urls import path

from . import views

app_name = 'intelligence'

urlpatterns = [
    path('skills/mastery/', views.SkillMasteryView.as_view(), name='skills-mastery'),
    path(
        'skills/recommendations/',
        views.SkillRecommendationsView.as_view(),
        name='skills-recommendations',
    ),
    path('tutor/generate/', views.GenerateTutorView.as_view(), name='tutor-generate'),
    path('tutor/latest/', views.LatestTutorView.as_view(), name='tutor-latest'),
    path('openended/submit/', views.OpenEndedSubmitView.as_view(), name='openended-submit'),
    path(
        'openended/<uuid:submission_id>/',
        views.OpenEndedDetailView.as_view(),
        name='openended-detail',
    ),
    path(
        'openended/<uuid:submission_id>/qa/',
        views.OpenEndedQAView.as_view(),
        name='openended-qa',
    ),
]

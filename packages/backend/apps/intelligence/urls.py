"""Task 6 — Intelligence (AI Diagnostika) URLs."""

from django.urls import path

from . import ai_admin_views, grading_views, views

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
    path('grading/queue/', grading_views.GradingQueueView.as_view(), name='grading-queue'),
    path('grading/queue/next/', grading_views.GradingNextView.as_view(), name='grading-next'),
    path(
        'grading/queue/<uuid:submission_id>/',
        grading_views.GradingDetailView.as_view(),
        name='grading-detail',
    ),
    path(
        'grading/queue/<uuid:submission_id>/grade/',
        grading_views.GradingGradeView.as_view(),
        name='grading-grade',
    ),
    path(
        'grading/calibration/', grading_views.CalibrationView.as_view(), name='grading-calibration'
    ),
    path('ai-providers/', ai_admin_views.ProviderListCreateView.as_view(), name='ai-providers'),
    path(
        'ai-providers/<int:pk>/',
        ai_admin_views.ProviderDetailView.as_view(),
        name='ai-provider-detail',
    ),
    path(
        'ai-providers/<int:pk>/test/',
        ai_admin_views.ProviderTestView.as_view(),
        name='ai-provider-test',
    ),
    path('mistakes/due/', views.MistakeDueListView.as_view(), name='mistakes-due'),
    path('mistakes/summary/', views.MistakeSummaryView.as_view(), name='mistakes-summary'),
    path(
        'mistakes/<uuid:card_id>/review/',
        views.MistakeReviewView.as_view(),
        name='mistakes-review',
    ),
]

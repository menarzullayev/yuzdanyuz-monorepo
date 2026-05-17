"""
Task 4 — Exam Engine REST API URLs.

Mounted under /api/exams/ via core/urls.py.
"""

from django.urls import path

from . import sse_views, views

app_name = 'exams'

urlpatterns = [
    # ISSUE-112: SSE migration Phase 1 — events stream + heartbeat
    path(
        'attempts/<uuid:attempt_id>/events/',
        sse_views.attempt_event_stream,
        name='attempt-events',
    ),
    path(
        'attempts/<uuid:attempt_id>/heartbeat/',
        sse_views.HeartbeatView.as_view(),
        name='attempt-heartbeat',
    ),
    # Mock exam endpoints
    path('mocks/', views.MockExamListView.as_view(), name='mock-list'),
    path('mocks/<uuid:pk>/', views.MockExamDetailView.as_view(), name='mock-detail'),
    path('mocks/<uuid:mock_id>/start/', views.StartAttemptView.as_view(), name='attempt-start'),
    # Attempt endpoints
    path('attempts/<uuid:attempt_id>/', views.AttemptDetailView.as_view(), name='attempt-detail'),
    path(
        'attempts/<uuid:attempt_id>/answer/',
        views.SubmitAnswerView.as_view(),
        name='attempt-answer',
    ),
    path(
        'attempts/<uuid:attempt_id>/submit/',
        views.SubmitAttemptView.as_view(),
        name='attempt-submit',
    ),
    path(
        'attempts/<uuid:attempt_id>/anticheat/',
        views.AntiCheatEventView.as_view(),
        name='attempt-anticheat',
    ),
    path(
        'attempts/<uuid:attempt_id>/dispute/',
        views.FileDisputeView.as_view(),
        name='attempt-dispute',
    ),
    # Practice endpoints
    path('practice/', views.PracticeCreateView.as_view(), name='practice-create'),
    path(
        'practice/<uuid:session_id>/',
        views.PracticeDetailView.as_view(),
        name='practice-detail',
    ),
    path(
        'practice/<uuid:session_id>/answer/',
        views.PracticeAnswerView.as_view(),
        name='practice-answer',
    ),
    path(
        'practice/<uuid:session_id>/finish/',
        views.PracticeFinishView.as_view(),
        name='practice-finish',
    ),
]

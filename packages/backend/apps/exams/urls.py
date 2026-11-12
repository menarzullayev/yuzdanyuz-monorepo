"""
Task 4 — Exam Engine REST API URLs.

Mounted under /api/exams/ via core/urls.py.
"""

from django.urls import path

from . import sitting_views, sse_views, views

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
    # Sittings (Rasch scoring + certificates)
    path('sittings/', sitting_views.SittingListCreateView.as_view(), name='sitting-list'),
    path('sittings/mine/', sitting_views.MyResultsView.as_view(), name='sitting-mine'),
    path('sittings/<uuid:pk>/', sitting_views.SittingDetailView.as_view(), name='sitting-detail'),
    path(
        'sittings/<uuid:pk>/my-result/',
        sitting_views.MyResultView.as_view(),
        name='sitting-my-result',
    ),
    path(
        'sittings/<uuid:pk>/results/',
        sitting_views.SittingResultsView.as_view(),
        name='sitting-results',
    ),
    path(
        'sittings/<uuid:pk>/open/',
        sitting_views.SittingActionView.as_view(action='open'),
        name='sitting-open',
    ),
    path(
        'sittings/<uuid:pk>/close/',
        sitting_views.SittingActionView.as_view(action='close'),
        name='sitting-close',
    ),
    path(
        'sittings/<uuid:pk>/calculate/',
        sitting_views.SittingActionView.as_view(action='calculate'),
        name='sitting-calculate',
    ),
    path(
        'sittings/<uuid:pk>/publish/',
        sitting_views.SittingActionView.as_view(action='publish'),
        name='sitting-publish',
    ),
    path('certificates/mine/', sitting_views.MyCertificatesView.as_view(), name='certificate-mine'),
    path(
        'certificates/verify/<str:number>/',
        sitting_views.CertificateVerifyView.as_view(),
        name='certificate-verify',
    ),
    path(
        'certificates/<uuid:pk>/revoke/',
        sitting_views.CertificateRevokeView.as_view(),
        name='certificate-revoke',
    ),
]

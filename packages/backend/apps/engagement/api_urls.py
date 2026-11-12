"""Arena, school rating, typing practice and live monitoring.

Mounted under /api/v1/engagement/ via core/urls.py.
"""

from django.urls import path

from . import arena_views, monitoring_views, rating_views, typing_views

app_name = 'engagement-api'

urlpatterns = [
    # Arena
    path('arena/quizzes/', arena_views.QuizListCreateView.as_view(), name='quiz-list'),
    path('arena/quizzes/<uuid:pk>/', arena_views.QuizDetailView.as_view(), name='quiz-detail'),
    *[
        path(
            f'arena/quizzes/<uuid:pk>/{action}/',
            arena_views.QuizActionView.as_view(action=action),
            name=f'quiz-{action}',
        )
        for action in ('submit', 'approve', 'reject', 'archive')
    ],
    path('arena/quizzes/<uuid:pk>/play/', arena_views.QuizPlayView.as_view(), name='quiz-play'),
    path(
        'arena/quizzes/<uuid:pk>/play/answer/',
        arena_views.QuizAnswerView.as_view(),
        name='quiz-answer',
    ),
    path(
        'arena/quizzes/<uuid:pk>/leaderboard/',
        arena_views.QuizLeaderboardView.as_view(),
        name='quiz-leaderboard',
    ),
    # School / learning-centre rating
    path('ratings/organizations/', rating_views.PublicRatingView.as_view(), name='rating-public'),
    path('ratings/mine/', rating_views.MyOrgRatingView.as_view(), name='rating-mine'),
    path('ratings/settings/', rating_views.RatingSettingsView.as_view(), name='rating-settings'),
    # Typing practice
    path('typing/texts/', typing_views.TextListView.as_view(), name='typing-texts'),
    path('typing/runs/', typing_views.RunListCreateView.as_view(), name='typing-runs'),
    path('typing/best/', typing_views.BestView.as_view(), name='typing-best'),
    path('typing/leaderboard/', typing_views.LeaderboardView.as_view(), name='typing-leaderboard'),
    # Live monitoring
    path(
        'monitoring/live-attempts/',
        monitoring_views.LiveAttemptsView.as_view(),
        name='monitoring-live',
    ),
]

"""
Task 5 — Engagement (Leaderboard) URLs.

Mounted under /api/leaderboard/ via core/urls.py.
"""

from django.urls import path

from . import views

app_name = 'engagement'

urlpatterns = [
    path('global/', views.GlobalLeaderboardView.as_view(), name='lb-global'),
    path('region/', views.RegionLeaderboardView.as_view(), name='lb-region'),
    path('tenant/', views.TenantLeaderboardView.as_view(), name='lb-tenant'),
    path('mock/<uuid:mock_id>/', views.MockLeaderboardView.as_view(), name='lb-mock'),
    path('me/', views.MyLeaderboardRankView.as_view(), name='lb-me'),
    path('history/', views.LeaderboardHistoryView.as_view(), name='lb-history'),
    # Task 9 — Engagement (streak, leagues, notifications, search)
    path('streak/', views.StreakView.as_view(), name='streak'),
    path('leagues/current/', views.CurrentLeagueView.as_view(), name='leagues-current'),
    path('leagues/history/', views.LeagueHistoryView.as_view(), name='leagues-history'),
    path('notifications/', views.NotificationListView.as_view(), name='notifications'),
    path(
        'notifications/<uuid:notification_id>/read/',
        views.NotificationReadView.as_view(),
        name='notification-read',
    ),
    path('search/', views.SearchView.as_view(), name='search'),
]

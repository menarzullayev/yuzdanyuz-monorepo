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
]

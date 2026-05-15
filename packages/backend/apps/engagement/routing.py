"""
Engagement WebSocket routing.

URLs:
  ws://host/ws/leaderboard/global/
  ws://host/ws/leaderboard/region/<region_id>/
  ws://host/ws/leaderboard/tenant/<org_id>/
  ws://host/ws/leaderboard/mock/<mock_id>/

Mounted via core/asgi.py ProtocolTypeRouter.
"""

from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/leaderboard/global/$', consumers.LeaderboardConsumer.as_asgi()),
    re_path(
        r'ws/leaderboard/region/(?P<region_id>\d+)/$',
        consumers.LeaderboardConsumer.as_asgi(),
    ),
    re_path(
        r'ws/leaderboard/tenant/(?P<org_id>[0-9a-f-]+)/$',
        consumers.LeaderboardConsumer.as_asgi(),
    ),
    re_path(
        r'ws/leaderboard/mock/(?P<mock_id>[0-9a-f-]+)/$',
        consumers.LeaderboardConsumer.as_asgi(),
    ),
]

"""
Exam WebSocket routing.

URL: ws://host/ws/exams/attempt/<attempt_id>/
Mounted via core/asgi.py ProtocolTypeRouter.
"""

from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(
        r'ws/exams/attempt/(?P<attempt_id>[0-9a-f-]+)/$',
        consumers.ExamAttemptConsumer.as_asgi(),
    ),
]

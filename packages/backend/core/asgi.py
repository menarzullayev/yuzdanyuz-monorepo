"""
ASGI config for core project — Django + Channels.

WebSocket router: apps/exams/routing.py exam consumer'lari.
HTTP traffic: standard Django ASGI app (Daphne/Uvicorn orqali).

Production:
    daphne -b 0.0.0.0 -p 8013 core.asgi:application
    # yoki
    uvicorn core.asgi:application --host 0.0.0.0 --port 8013
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
os.environ.setdefault('DJANGO_ENV', 'prod')

# IMPORTANT: get_asgi_application() Django'ni boot qiladi.
# Channels-related import'lar BUNDAN KEYIN qilinishi kerak,
# aks holda AppRegistryNotReady error chiqadi.
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from apps.engagement.routing import websocket_urlpatterns as engagement_ws  # noqa: E402
from apps.exams.routing import websocket_urlpatterns as exams_ws  # noqa: E402

application = ProtocolTypeRouter(
    {
        'http': django_asgi_app,
        'websocket': AuthMiddlewareStack(URLRouter([*exams_ws, *engagement_ws])),
    }
)

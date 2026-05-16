"""
Liveness/readiness probes for K8s.

  /health/        — liveness (always 200 if process is running)
  /health/ready/  — readiness (200 only if DB + Redis reachable)

Cheap, dependency-free, no auth — exposed on the internal port; restrict at
ingress/network policy level if needed.
"""

import logging

from django.conf import settings
from django.db import connection
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def liveness(request):
    return JsonResponse({'status': 'ok'}, status=200)


def readiness(request):
    checks: dict[str, bool] = {}

    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        checks['database'] = True
    except Exception as exc:
        logger.warning('readiness: db check failed: %s', exc)
        checks['database'] = False

    try:
        import redis

        r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
        r.ping()
        checks['redis'] = True
    except Exception as exc:
        logger.warning('readiness: redis check failed: %s', exc)
        checks['redis'] = False

    healthy = all(checks.values())
    return JsonResponse(
        {'status': 'ready' if healthy else 'degraded', 'checks': checks},
        status=200 if healthy else 503,
    )

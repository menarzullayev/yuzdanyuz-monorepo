"""
Task 10 — Observability stack initialization.

  init_sentry()      — Sentry SDK (DSN env-driven; no-op if DSN missing)
  json_logging_dict() — JSON LOGGING dict for prod settings
  PROMETHEUS_*       — django-prometheus app name + middleware (use in INSTALLED_APPS / MIDDLEWARE)

OpenTelemetry is wired at runtime via `opentelemetry-instrument` (entrypoint.sh),
so no Python init needed here — just ensure OTEL_* env vars are set in the pod.
"""

from __future__ import annotations

import logging
import os
from typing import Any


def init_sentry() -> bool:
    """Initialize Sentry if SENTRY_DSN is set. Idempotent / safe to call always."""
    dsn = os.getenv('SENTRY_DSN', '').strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.redis import RedisIntegration
    except ImportError:
        logging.getLogger(__name__).warning('sentry-sdk not installed, skipping init')
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv('SENTRY_ENVIRONMENT', os.getenv('DJANGO_ENV', 'prod')),
        release=os.getenv('SENTRY_RELEASE') or os.getenv('GIT_COMMIT_SHA', 'unknown'),
        traces_sample_rate=float(os.getenv('SENTRY_TRACES_SAMPLE_RATE', '0.1')),
        profiles_sample_rate=float(os.getenv('SENTRY_PROFILES_SAMPLE_RATE', '0.0')),
        send_default_pii=False,
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(),
            RedisIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
    )
    return True


def json_logging_dict(level: str = 'INFO') -> dict[str, Any]:
    """
    Production LOGGING dict — JSON formatter + console handler. Friendly to Loki,
    Datadog, CloudWatch. Stdlib only fallback if python-json-logger is absent
    (runtime crash beats silent unstructured logs).
    """
    return {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'json': {
                '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
                'fmt': '%(asctime)s %(levelname)s %(name)s %(module)s %(message)s',
                'rename_fields': {'asctime': 'time', 'levelname': 'level'},
            },
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'json',
                'stream': 'ext://sys.stdout',
            },
        },
        'loggers': {
            'django.request': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
            'django.db.backends': {
                'handlers': ['console'],
                'level': os.getenv('DB_LOG_LEVEL', 'WARNING'),
                'propagate': False,
            },
            'celery': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        },
        'root': {'handlers': ['console'], 'level': level},
    }

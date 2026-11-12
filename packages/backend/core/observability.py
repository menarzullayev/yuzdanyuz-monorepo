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
import re
from typing import Any

from pythonjsonlogger.json import JsonFormatter

# Secrets that end up inside URLs: Telegram puts the bot token in the path
# (https://api.telegram.org/bot<id>:<secret>/getMe), and some providers take keys
# as query parameters. httpx logs every request URL at INFO; Sentry's httpx and
# logging integrations copy them into breadcrumbs.
_SECRET_PATTERNS = (
    (re.compile(r'bot\d+:[A-Za-z0-9_-]+'), 'bot<redacted>'),
    (
        re.compile(
            r'(?i)([?&](?:token|access_token|api_key|apikey|key|secret|password|signature)=)'
            r'[^&\s"\']+'
        ),
        r'\1<redacted>',
    ),
)

# Third-party loggers that write full request URLs at INFO.
_NOISY_URL_LOGGERS = ('httpx', 'httpcore', 'httpx2', 'urllib3')


def redact(text: str) -> str:
    """Remove known secret shapes (bot tokens, key=... query params) from text."""
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(v) for v in value)
    return value


def scrub_breadcrumb(crumb: dict, hint: dict) -> dict:
    """Sentry before_breadcrumb: strip secrets from URLs and messages."""
    return _redact_value(crumb)


def scrub_event(event: dict, hint: dict) -> dict:
    """Sentry before_send: strip secrets from messages, exceptions and breadcrumbs."""
    return _redact_value(event)


class RedactingJsonFormatter(JsonFormatter):
    """JSON formatter that redacts secrets from the final line (message, extras, traceback)."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


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
        # No PII: with neither `data_collection` nor the deprecated `send_default_pii`
        # set, sentry-sdk behaves as send_default_pii=False.
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(),
            RedisIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        before_breadcrumb=scrub_breadcrumb,
        before_send=scrub_event,
    )
    return True


def quiet_url_loggers() -> dict[str, dict[str, Any]]:
    """LOGGING entries that keep URL-logging HTTP clients at WARNING (no request URLs)."""
    return {name: {'level': 'WARNING'} for name in _NOISY_URL_LOGGERS}


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
                '()': 'core.observability.RedactingJsonFormatter',
                'fmt': '%(asctime)s %(levelname)s %(name)s %(module)s %(message)s',
                'rename_fields': {'asctime': 'time', 'levelname': 'level'},
            },
        },
        'filters': {
            'request_id': {'()': 'core.middleware.request_id.RequestIdFilter'},
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'json',
                'filters': ['request_id'],
                'stream': 'ext://sys.stdout',
            },
        },
        'loggers': {
            # One JSON line per HTTP request (core.middleware.request_id).
            'yuzdanyuz.access': {
                'handlers': ['console'],
                'level': os.getenv('ACCESS_LOG_LEVEL', 'INFO'),
                'propagate': False,
            },
            **quiet_url_loggers(),
            'django.request': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
            'django.db.backends': {
                'handlers': ['console'],
                'level': os.getenv('DB_LOG_LEVEL', 'WARNING'),
                'propagate': False,
            },
            'celery': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
            # ISSUE-X06: structured key-value events via core.logging.log_event().
            # propagate=False — avoid double-emission through root.
            'yuzdanyuz.events': {
                'handlers': ['console'],
                'level': os.getenv('EVENTS_LOG_LEVEL', 'INFO'),
                'propagate': False,
            },
        },
        'root': {'handlers': ['console'], 'level': level},
    }

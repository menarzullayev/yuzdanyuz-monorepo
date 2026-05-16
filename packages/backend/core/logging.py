"""ISSUE-X06 — Structured logging helper.

Wraps stdlib logging with key-value structured fields. Production log
aggregators (Loki / CloudWatch / Datadog) index these fields and enable
queries like `level=error AND event=payment_failed AND org_id=<uuid>`.

Usage:
    from core.logging import log_event

    log_event('info', 'exam_submitted',
              user_id=str(user.id),
              org_id=str(org.id),
              score=92.5,
              duration_seconds=1800)

The 'event' field is the canonical event name (snake_case). All structured
fields go into the LogRecord's `extra=` dict and python-json-logger emits
them as top-level JSON keys.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

_LEVEL_MAP = {
    'debug': logging.DEBUG,
    'info': logging.INFO,
    'warning': logging.WARNING,
    'error': logging.ERROR,
    'critical': logging.CRITICAL,
}

Level = Literal['debug', 'info', 'warning', 'error', 'critical']

# Canonical logger name — wired to JSON handler in prod (core.observability)
# va base.LOGGING'da. Boshqa joyda 'yuzdanyuz.events' deb hard-code qilmang —
# shu module orqali yuboring.
EVENT_LOGGER_NAME = 'yuzdanyuz.events'


def log_event(level: Level, event: str, **fields: Any) -> None:
    """Emit a structured log event with key-value fields.

    Args:
        level: 'debug'/'info'/'warning'/'error'/'critical'. Invalid → INFO.
        event: snake_case event name (e.g. 'exam_submitted', 'payment_failed').
        **fields: arbitrary structured fields (must be JSON-serializable).
    """
    logger = logging.getLogger(EVENT_LOGGER_NAME)
    log_level = _LEVEL_MAP.get(level.lower(), logging.INFO)
    # event=<name> uchun extra'ga qo'shamiz (json formatter top-level key qiladi).
    # message ham 'event' nomi bo'ladi — plain-text loglarda ham search qulay.
    extra = {'event': event, **fields}
    logger.log(log_level, event, extra=extra)

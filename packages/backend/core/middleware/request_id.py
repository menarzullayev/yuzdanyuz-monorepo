"""Request id, structured access log and Sentry scope (audit G01).

Every request gets an id: a well-formed incoming ``X-Request-ID`` (from nginx,
Cloudflare or a client) is kept, anything else is replaced with a fresh uuid4.
The id is

- returned as the ``X-Request-ID`` response header,
- attached to every log record written while the request runs (``RequestIdFilter``),
- set as the Sentry ``request_id`` tag, with the user id as the Sentry user,
- written with method, path, status, latency and user id as one access-log line
  on the ``yuzdanyuz.access`` logger (JSON in prod).

The path is logged without the query string, which can carry tokens.
Successful probe and scrape requests are not logged (one line per pod every few
seconds would drown everything else); failed ones are.
"""

from __future__ import annotations

import contextvars
import logging
import re
import time
import uuid

import sentry_sdk

ACCESS_LOGGER_NAME = 'yuzdanyuz.access'
HEADER = 'X-Request-ID'

_VALID_ID = re.compile(r'^[A-Za-z0-9._-]{1,128}$')
_QUIET_PATHS = frozenset({'/health/', '/health/ready/', '/metrics'})

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar('request_id', default=None)

access_logger = logging.getLogger(ACCESS_LOGGER_NAME)


def get_request_id() -> str | None:
    """The current request's id, or None outside a request."""
    return _request_id.get()


class RequestIdFilter(logging.Filter):
    """Adds ``request_id`` to every record so the JSON formatter emits it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, 'request_id'):
            record.request_id = _request_id.get()
        return True


def _user_id(request) -> str | None:
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return str(user.pk)
    return None


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get(HEADER, '')
        request_id = incoming if _VALID_ID.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        token = _request_id.set(request_id)
        sentry_sdk.set_tag('request_id', request_id)
        started = time.perf_counter()
        status = 500
        try:
            response = self.get_response(request)
            status = response.status_code
            response[HEADER] = request_id
            return response
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            if status >= 400 or request.path_info not in _QUIET_PATHS:
                self._log(request, request_id, status, duration_ms)
            _request_id.reset(token)

    def process_view(self, request, view_func, view_args, view_kwargs):
        # Authentication middleware has run by now; errors raised by the view are
        # reported to Sentry with the user id (never email or name: no PII).
        user_id = _user_id(request)
        if user_id is not None:
            sentry_sdk.set_user({'id': user_id})
        return None

    @staticmethod
    def _log(request, request_id: str, status: int, duration_ms: float) -> None:
        # WARNING, not ERROR: Sentry already reports the exception behind a 5xx.
        level = logging.WARNING if status >= 500 else logging.INFO
        access_logger.log(
            level,
            '%s %s %s',
            request.method,
            request.path,
            status,
            extra={
                'request_id': request_id,
                'method': request.method,
                'path': request.path,
                'status': status,
                'duration_ms': duration_ms,
                'user_id': _user_id(request),
            },
        )

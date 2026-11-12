"""G01 — the Telegram bot token must never reach logs or Sentry.

`core.health._check_telegram` calls `https://api.telegram.org/bot<TOKEN>/getMe`.
httpx logs every request URL at INFO, and the prod root logger is INFO, so the
token used to land in stdout (and in Sentry breadcrumbs) — and anyone could
trigger it through the unauthenticated `/health/ready/?deep=1`.
"""

import logging

import httpx
import pytest
from django.conf import settings
from django.test import Client

from core import health, observability
from tests.logging_snapshot import prod_json_logging

TOKEN = '123456789:AAFakeTokenForLeakTest_abcdefghijklmn'


@pytest.fixture
def telegram_mock(monkeypatch):
    """Route every httpx.Client through a MockTransport that answers getMe."""
    calls = []
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == 'api.telegram.org':
            return httpx.Response(200, json={'ok': True, 'result': {'id': 1}})
        return httpx.Response(404)

    def client_factory(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, 'Client', client_factory)
    monkeypatch.setattr(settings, 'TELEGRAM_BOT_TOKEN', TOKEN)
    return calls


@pytest.fixture
def prod_logging():
    """Apply the real production LOGGING dict (root at DEBUG), writing into a buffer."""
    with prod_json_logging(level='DEBUG') as buf:
        yield buf


@pytest.mark.security
class TestDeepReadinessIsNotPublic:
    def test_anonymous_deep_check_is_refused(self, db, telegram_mock):
        resp = Client().get('/health/ready/?deep=1')
        assert resp.status_code == 403
        assert telegram_mock == [], 'anonymous request reached api.telegram.org'

    def test_wrong_token_is_refused(self, db, telegram_mock, settings):
        settings.HEALTH_DEEP_TOKEN = 'right-token'
        resp = Client().get('/health/ready/?deep=1', HTTP_X_HEALTH_TOKEN='wrong')
        assert resp.status_code == 403
        assert telegram_mock == []

    def test_deep_check_with_token_runs(self, db, telegram_mock, settings):
        settings.HEALTH_DEEP_TOKEN = 'right-token'
        resp = Client().get('/health/ready/?deep=1', HTTP_X_HEALTH_TOKEN='right-token')
        assert resp.status_code in (200, 503)
        assert resp.json()['checks']['telegram'] is True

    def test_empty_configured_token_never_matches(self, db, telegram_mock, settings):
        settings.HEALTH_DEEP_TOKEN = ''
        resp = Client().get('/health/ready/?deep=1', HTTP_X_HEALTH_TOKEN='')
        assert resp.status_code == 403

    def test_quick_check_stays_public(self, db):
        resp = Client().get('/health/ready/')
        assert resp.status_code in (200, 503)


@pytest.mark.security
class TestTokenNeverLogged:
    def test_telegram_check_does_not_log_token(self, telegram_mock, prod_logging):
        assert health._check_telegram() is True
        assert telegram_mock, 'the mock was not reached; the test proves nothing'
        out = prod_logging.getvalue()
        assert TOKEN not in out
        assert TOKEN.split(':', 1)[1] not in out

    def test_failed_telegram_check_does_not_log_token(self, monkeypatch, prod_logging):
        real_client = httpx.Client

        def boom(request):
            raise httpx.ConnectError(f'cannot reach {request.url}', request=request)

        monkeypatch.setattr(
            httpx,
            'Client',
            lambda *a, **k: real_client(*a, transport=httpx.MockTransport(boom), **k),
        )
        monkeypatch.setattr(settings, 'TELEGRAM_BOT_TOKEN', TOKEN)
        assert health._check_telegram() is False
        out = prod_logging.getvalue()
        assert 'telegram check failed' in out
        assert TOKEN not in out
        assert TOKEN.split(':', 1)[1] not in out

    def test_any_logger_redacts_bot_urls(self, prod_logging):
        logging.getLogger('some.library').warning(
            'GET https://api.telegram.org/bot%s/sendMessage failed', TOKEN
        )
        out = prod_logging.getvalue()
        assert TOKEN not in out
        assert 'bot<redacted>' in out


@pytest.mark.security
class TestSentryScrubsToken:
    def test_breadcrumb_url_and_message_are_scrubbed(self):
        crumb = {
            'category': 'httplib',
            'message': f'HTTP Request: GET https://api.telegram.org/bot{TOKEN}/getMe',
            'data': {'url': f'https://api.telegram.org/bot{TOKEN}/getMe', 'method': 'GET'},
        }
        out = observability.scrub_breadcrumb(crumb, {})
        assert TOKEN not in repr(out)

    def test_event_is_scrubbed(self):
        event = {
            'message': f'boom bot{TOKEN}',
            'exception': {
                'values': [{'value': f'cannot reach https://api.telegram.org/bot{TOKEN}/getMe'}]
            },
            'breadcrumbs': {'values': [{'message': f'bot{TOKEN}'}]},
        }
        out = observability.scrub_event(event, {})
        assert TOKEN not in repr(out)

"""Sentry sees each Anthropic SDK request exactly once, with the real prod init.

The Anthropic SDK 2.x sends requests through httpx2. sentry-sdk 2.25 only
patched httpx, so core/__init__.py used to alias httpx to httpx2 (#18). From
sentry-sdk 2.62 httpx2 is instrumented natively, and with the alias still in
place every SDK request produced two breadcrumbs (measured on 2.74.0). The alias
is gone; these tests keep both failure modes out: zero records and double records.
"""

import warnings

import anthropic
import httpx2
import pytest
import sentry_sdk

from core import observability

ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages'


def _reply(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            'id': 'msg_test',
            'type': 'message',
            'role': 'assistant',
            'model': 'claude-sonnet-5-5',
            'content': [{'type': 'text', 'text': 'ok'}],
            'stop_reason': 'end_turn',
            'stop_sequence': None,
            'usage': {'input_tokens': 1, 'output_tokens': 1},
        },
    )


@pytest.fixture
def prod_sentry(monkeypatch):
    """Run the real init_sentry() against an unroutable DSN and collect breadcrumbs."""
    crumbs = []
    real_init = sentry_sdk.init

    def init_keeping_breadcrumbs(**options):
        def keep(crumb, hint):
            crumbs.append(crumb)
            return crumb

        return real_init(before_breadcrumb=keep, **options)

    monkeypatch.setenv('SENTRY_DSN', 'https://public@sentry.invalid/1')
    monkeypatch.setattr(sentry_sdk, 'init', init_keeping_breadcrumbs)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        assert observability.init_sentry() is True
    monkeypatch.setattr(sentry_sdk, 'init', real_init)
    try:
        yield crumbs, caught
    finally:
        # No events are captured, so nothing is sent; back to a disabled client.
        sentry_sdk.get_client().close()
        sentry_sdk.init()


@pytest.mark.unit
def test_httpx_is_not_aliased_to_httpx2():
    import httpx

    assert httpx is not httpx2
    assert anthropic.DefaultHttpxClient.__module__.startswith('anthropic')
    assert issubclass(anthropic.DefaultHttpxClient, httpx2.Client)


@pytest.mark.unit
def test_prod_init_records_each_anthropic_request_once(prod_sentry):
    crumbs, _ = prod_sentry
    client = anthropic.Anthropic(
        api_key='test-key-not-real',
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(_reply)),
        max_retries=0,
    )

    client.messages.create(
        model='claude-sonnet-5-5',
        max_tokens=16,
        messages=[{'role': 'user', 'content': 'hi'}],
    )

    http = [c for c in crumbs if c.get('category') == 'httplib']
    assert [(c['data'].get('url'), c['data'].get('http.method')) for c in http] == [
        (ANTHROPIC_URL, 'POST')
    ], crumbs


@pytest.mark.unit
def test_prod_init_uses_no_deprecated_options(prod_sentry):
    _, caught = prod_sentry

    deprecated = [
        str(w.message)
        for w in caught
        if issubclass(w.category, (DeprecationWarning, FutureWarning))
    ]
    assert deprecated == []


@pytest.mark.unit
def test_init_without_dsn_does_nothing(monkeypatch):
    monkeypatch.delenv('SENTRY_DSN', raising=False)

    assert observability.init_sentry() is False

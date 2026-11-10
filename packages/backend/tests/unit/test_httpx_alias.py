"""httpx is aliased to httpx2 (core/__init__.py), so httpx-patching tools see SDK calls.

The Anthropic SDK 2.x uses httpx2. Without the alias, Sentry's HttpxIntegration
patches the old httpx package and silently records nothing for Claude requests.
"""

import anthropic
import httpx2
import pytest


@pytest.mark.unit
def test_httpx_resolves_to_httpx2():
    import httpx

    assert httpx is httpx2
    assert httpx.Client is httpx2.Client
    # The SDK's default client is the same class tools patch through `httpx`.
    assert issubclass(anthropic.DefaultHttpxClient, httpx.Client)


@pytest.mark.unit
def test_sentry_httpx_integration_records_anthropic_requests():
    sentry_sdk = pytest.importorskip('sentry_sdk')  # prod-only dependency
    from sentry_sdk.integrations.httpx import HttpxIntegration

    crumbs = []

    def keep(crumb, hint):
        crumbs.append(crumb)
        return crumb

    def handler(request: httpx2.Request) -> httpx2.Response:
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

    # No events are captured, so nothing is sent to the (unroutable) DSN.
    sentry_sdk.init(
        dsn='https://public@sentry.invalid/1',
        default_integrations=False,
        integrations=[HttpxIntegration()],
        before_breadcrumb=keep,
    )
    try:
        client = anthropic.Anthropic(
            api_key='test-key-not-real',
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
            max_retries=0,
        )
        client.messages.create(
            model='claude-sonnet-5-5',
            max_tokens=16,
            messages=[{'role': 'user', 'content': 'hi'}],
        )
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.init()  # back to a disabled client for the rest of the run

    http_crumbs = [c for c in crumbs if c.get('category') == 'httplib']
    assert any(
        c['data'].get('url') == 'https://api.anthropic.com/v1/messages'
        and c['data'].get('http.method') == 'POST'
        for c in http_crumbs
    ), crumbs

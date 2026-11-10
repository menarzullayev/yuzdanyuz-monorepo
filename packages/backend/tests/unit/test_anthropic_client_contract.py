"""Request/response contract of the two Claude call sites against the real SDK.

The SDK runs for real; only its HTTP transport is replaced (httpx2.MockTransport),
so these tests catch SDK upgrades that change what goes over the wire - the API
version, the auth header, removed parameters - without network or credentials.
"""

import json

import anthropic
import httpx2
import pytest

from apps.catalog.services.ai_providers.base import SYSTEM_PROMPT
from apps.catalog.services.ai_providers.claude import AnthropicProvider
from apps.intelligence.tasks import _generate_text

API_VERSION = '2026-10-21'
FAKE_KEY = 'test-key-not-real'


def _message(text: str, model: str = 'claude-sonnet-4-6') -> dict:
    return {
        'id': 'msg_test',
        'type': 'message',
        'role': 'assistant',
        'model': model,
        'content': [{'type': 'text', 'text': text}],
        'stop_reason': 'end_turn',
        'stop_sequence': None,
        'usage': {'input_tokens': 10, 'output_tokens': 5},
    }


@pytest.fixture
def claude_transport(monkeypatch):
    """Route every anthropic.Anthropic(...) built by app code through a mock transport."""
    state = {'requests': [], 'reply': None, 'status': 200}

    def handler(request: httpx2.Request) -> httpx2.Response:
        state['requests'].append(request)
        if state['status'] != 200:
            return httpx2.Response(state['status'], json={'type': 'error', 'error': {}})
        return httpx2.Response(200, json=state['reply'])

    real_client = anthropic.Anthropic

    def client_factory(**kwargs):
        http_client = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
        return real_client(http_client=http_client, max_retries=0, **kwargs)

    monkeypatch.setattr(anthropic, 'Anthropic', client_factory)
    return state


def _assert_wire_contract(request: httpx2.Request) -> dict:
    assert request.method == 'POST'
    assert str(request.url) == 'https://api.anthropic.com/v1/messages'
    assert request.headers['anthropic-version'] == API_VERSION
    assert request.headers['authorization'] == f'Bearer {FAKE_KEY}'
    assert 'x-api-key' not in request.headers
    body = json.loads(request.content)
    # Removed on 2026-10-21: any of these is a 400 on every model.
    assert not {'temperature', 'top_p', 'top_k'} & body.keys()
    return body


@pytest.mark.unit
class TestAnthropicProviderContract:
    def test_parser_request_and_json_reply(self, claude_transport):
        claude_transport['reply'] = _message('{"text": "2 + 2 = ?", "options": []}')

        result = AnthropicProvider(api_key=FAKE_KEY, model='claude-sonnet-4-6').parse_text_block(
            'Tuzilmagan blok'
        )

        assert result == {'text': '2 + 2 = ?', 'options': []}
        (request,) = claude_transport['requests']
        body = _assert_wire_contract(request)
        assert body['model'] == 'claude-sonnet-4-6'
        assert body['max_tokens'] == 1024
        assert body['system'] == [
            {'type': 'text', 'text': SYSTEM_PROMPT, 'cache_control': {'type': 'ephemeral'}}
        ]
        assert body['messages'][0]['role'] == 'user'
        assert 'Tuzilmagan blok' in body['messages'][0]['content']

    def test_parser_returns_error_dict_on_api_failure(self, claude_transport):
        claude_transport['status'] = 500

        result = AnthropicProvider(api_key=FAKE_KEY, model='claude-sonnet-4-6').parse_text_block(
            'blok'
        )

        assert set(result) == {'error', 'raw_text'}
        assert result['raw_text'] == 'blok'

    def test_parser_reports_non_json_reply(self, claude_transport):
        claude_transport['reply'] = _message('bu JSON emas')

        result = AnthropicProvider(api_key=FAKE_KEY, model='claude-sonnet-4-6').parse_text_block(
            'blok'
        )

        assert result['error'].startswith('JSON parse xatosi')


@pytest.mark.unit
class TestGenerateTextContract:
    def test_tutor_call_with_cached_system_prompt(self, claude_transport, settings):
        settings.ANTHROPIC_API_KEY = FAKE_KEY
        claude_transport['reply'] = _message('Zor natija!')

        text = _generate_text('prompt', max_tokens=512, model='claude-sonnet-4-6', system='SYS')

        assert text == 'Zor natija!'
        (request,) = claude_transport['requests']
        body = _assert_wire_contract(request)
        assert body['max_tokens'] == 512
        assert body['system'] == [
            {'type': 'text', 'text': 'SYS', 'cache_control': {'type': 'ephemeral'}}
        ]
        assert body['messages'] == [{'role': 'user', 'content': 'prompt'}]

    def test_without_system_prompt_sends_no_system_field(self, claude_transport, settings):
        settings.ANTHROPIC_API_KEY = FAKE_KEY
        claude_transport['reply'] = _message('ok')

        assert _generate_text('prompt', model='claude-sonnet-4-6') == 'ok'
        body = _assert_wire_contract(claude_transport['requests'][0])
        assert 'system' not in body

    def test_missing_api_key_fails_before_any_request(self, claude_transport, settings):
        settings.ANTHROPIC_API_KEY = ''

        with pytest.raises(RuntimeError):
            _generate_text('prompt')
        assert claude_transport['requests'] == []

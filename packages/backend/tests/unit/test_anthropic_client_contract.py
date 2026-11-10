"""Request/response contract of the two Claude call sites against the real SDK.

The SDK runs for real; only its HTTP transport is replaced (httpx2.MockTransport),
so these tests catch SDK upgrades and model migrations that change what goes over
the wire - the API version, the auth header, removed parameters, thinking blocks
in the reply - without network or credentials.
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
FALLBACK_BETA = 'server-side-fallback-2026-07-01'
THINKING_BLOCK = {'type': 'thinking', 'thinking': '', 'signature': 'sig-test'}


def _message(
    text: str | None = None,
    *,
    model: str = 'claude-sonnet-5-5',
    content: list | None = None,
    stop_reason: str = 'end_turn',
    stop_details: dict | None = None,
) -> dict:
    if content is None:
        content = [{'type': 'text', 'text': text}]
    return {
        'id': 'msg_test',
        'type': 'message',
        'role': 'assistant',
        'model': model,
        'content': content,
        'stop_reason': stop_reason,
        'stop_sequence': None,
        'stop_details': stop_details,
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
    assert request.url.host == 'api.anthropic.com'
    assert request.url.path == '/v1/messages'
    assert request.headers['anthropic-version'] == API_VERSION
    assert request.headers['authorization'] == f'Bearer {FAKE_KEY}'
    assert 'x-api-key' not in request.headers
    body = json.loads(request.content)
    # Removed on 2026-10-21: any of these is a 400 on every model.
    assert not {'temperature', 'top_p', 'top_k'} & body.keys()
    # Prefill (a trailing assistant turn) is a 400 on Claude 4.6 and later.
    assert body['messages'][-1]['role'] == 'user'
    return body


def _parser(model: str = 'claude-sonnet-4-6') -> AnthropicProvider:
    return AnthropicProvider(api_key=FAKE_KEY, model=model)


@pytest.mark.unit
class TestAnthropicProviderContract:
    def test_parser_request_and_json_reply(self, claude_transport):
        claude_transport['reply'] = _message('{"text": "2 + 2 = ?", "options": []}')

        result = _parser().parse_text_block('Tuzilmagan blok')

        assert result == {'text': '2 + 2 = ?', 'options': []}
        (request,) = claude_transport['requests']
        body = _assert_wire_contract(request)
        assert body['model'] == 'claude-sonnet-4-6'
        assert body['max_tokens'] == 4096
        assert body['system'] == [
            {'type': 'text', 'text': SYSTEM_PROMPT, 'cache_control': {'type': 'ephemeral'}}
        ]
        assert 'Tuzilmagan blok' in body['messages'][0]['content']

    def test_parser_reads_text_after_a_thinking_block(self, claude_transport):
        claude_transport['reply'] = _message(
            content=[THINKING_BLOCK, {'type': 'text', 'text': '{"text": "Q", "options": []}'}]
        )

        assert _parser('claude-sonnet-5-5').parse_text_block('blok') == {
            'text': 'Q',
            'options': [],
        }

    def test_parser_turns_refusal_into_error_dict(self, claude_transport):
        claude_transport['reply'] = _message(
            content=[], stop_reason='refusal', stop_details={'type': 'refusal', 'category': 'cyber'}
        )

        result = _parser('claude-sonnet-5-5').parse_text_block('blok')

        assert 'category=cyber' in result['error']
        assert result['raw_text'] == 'blok'

    def test_parser_returns_error_dict_on_api_failure(self, claude_transport):
        claude_transport['status'] = 500

        result = _parser().parse_text_block('blok')

        assert set(result) == {'error', 'raw_text'}
        assert result['raw_text'] == 'blok'

    def test_parser_reports_non_json_reply(self, claude_transport):
        claude_transport['reply'] = _message('bu JSON emas')

        result = _parser().parse_text_block('blok')

        assert result['error'].startswith('JSON parse xatosi')


@pytest.mark.unit
class TestGenerateTextContract:
    @pytest.fixture(autouse=True)
    def _settings(self, settings):
        settings.ANTHROPIC_API_KEY = FAKE_KEY
        settings.ANTHROPIC_MODEL = 'claude-sonnet-5-5'

    def test_default_model_request_with_fallback_and_low_effort(self, claude_transport):
        claude_transport['reply'] = _message('Zor natija!')

        assert _generate_text('prompt', system='SYS') == 'Zor natija!'

        (request,) = claude_transport['requests']
        body = _assert_wire_contract(request)
        assert body['model'] == 'claude-sonnet-5-5'
        assert body['max_tokens'] == 2048
        assert body['output_config'] == {'effort': 'low'}
        assert 'thinking' not in body
        assert body['system'] == [
            {'type': 'text', 'text': 'SYS', 'cache_control': {'type': 'ephemeral'}}
        ]
        assert body['messages'] == [{'role': 'user', 'content': 'prompt'}]
        assert body['fallbacks'] == 'default'
        assert FALLBACK_BETA in request.headers['anthropic-beta']

    def test_anthropic_model_setting_is_honoured(self, claude_transport, settings):
        settings.ANTHROPIC_MODEL = 'claude-opus-5-5'
        claude_transport['reply'] = _message('ok', model='claude-opus-5-5')

        _generate_text('prompt')

        assert json.loads(claude_transport['requests'][0].content)['model'] == 'claude-opus-5-5'

    def test_no_fallback_for_models_without_server_side_fallback(self, claude_transport):
        claude_transport['reply'] = _message('ok', model='claude-sonnet-4-6')

        assert _generate_text('prompt', model='claude-sonnet-4-6') == 'ok'

        request = claude_transport['requests'][0]
        body = _assert_wire_contract(request)
        assert 'fallbacks' not in body
        assert 'system' not in body
        assert FALLBACK_BETA not in request.headers.get('anthropic-beta', '')

    def test_reads_text_after_a_thinking_block(self, claude_transport):
        claude_transport['reply'] = _message(
            content=[THINKING_BLOCK, {'type': 'text', 'text': 'Davom et!'}]
        )

        assert _generate_text('prompt') == 'Davom et!'

    def test_refusal_raises(self, claude_transport):
        claude_transport['reply'] = _message(
            content=[], stop_reason='refusal', stop_details={'type': 'refusal', 'category': 'bio'}
        )

        with pytest.raises(RuntimeError, match='category=bio'):
            _generate_text('prompt')

    def test_truncated_reply_raises(self, claude_transport):
        claude_transport['reply'] = _message('{"grammar": 8', stop_reason='max_tokens')

        with pytest.raises(RuntimeError, match='max_tokens'):
            _generate_text('prompt')

    def test_missing_api_key_fails_before_any_request(self, claude_transport, settings):
        settings.ANTHROPIC_API_KEY = ''

        with pytest.raises(RuntimeError):
            _generate_text('prompt')
        assert claude_transport['requests'] == []

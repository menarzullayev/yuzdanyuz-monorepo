"""AI provider layer: key encryption, profile selection/fallback, HTTP providers (mocked)."""

import httpx
import pytest

from apps.catalog.models import AIProviderConfig
from apps.catalog.services.ai_providers import ProviderFactory
from apps.catalog.services.ai_providers.base import TransientProviderError
from apps.intelligence import llm
from core import crypto

pytestmark = pytest.mark.django_db

SECRET = 'fake-provider-key-1234'  # obviously fake, low entropy (gitleaks)


def _config(provider='openai', use_for='all', key=SECRET, **kw):
    return AIProviderConfig.objects.create(
        name=kw.pop('name', f'{provider}-{use_for}'),
        provider=provider,
        model=kw.pop('model', 'm1'),
        api_key=key,
        use_for=use_for,
        **kw,
    )


class FakeHttp:
    """Replaces httpx.post/get; records calls and returns queued (status, json) replies."""

    def __init__(self, monkeypatch):
        self.calls = []
        self.replies = []
        monkeypatch.setattr(httpx, 'post', self._send)
        monkeypatch.setattr(httpx, 'get', self._send)

    def queue(self, status, body):
        self.replies.append((status, body))

    def _send(self, url, **kwargs):
        self.calls.append({'url': url, **kwargs})
        status, body = self.replies.pop(0)
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body, request=httpx.Request('POST', url))


@pytest.fixture
def http(monkeypatch):
    return FakeHttp(monkeypatch)


class TestEncryption:
    def test_round_trip(self):
        token = crypto.encrypt(SECRET)
        assert token.startswith(crypto.PREFIX)
        assert SECRET not in token
        assert crypto.decrypt(token) == SECRET

    def test_empty_stays_empty_and_legacy_plaintext_passes_through(self):
        assert crypto.encrypt('') == ''
        assert crypto.decrypt('plain-old-key') == 'plain-old-key'

    def test_tampered_token_is_rejected(self):
        with pytest.raises(crypto.DecryptionError):
            crypto.decrypt(crypto.PREFIX + 'not-a-fernet-token')

    def test_key_from_other_secret_cannot_decrypt(self, settings):
        token = crypto.encrypt(SECRET)
        settings.FIELD_ENCRYPTION_KEY = ''
        settings.SECRET_KEY = 'a-different-secret-key'
        with pytest.raises(crypto.DecryptionError):
            crypto.decrypt(token)

    def test_model_stores_ciphertext_and_decrypts_on_demand(self):
        cfg = _config()
        cfg.refresh_from_db()
        assert cfg.api_key != SECRET
        assert SECRET not in cfg.api_key
        assert cfg.api_key_hint == SECRET[-4:]
        assert cfg.get_api_key() == SECRET
        raw = AIProviderConfig.objects.values_list('api_key', flat=True).get(pk=cfg.pk)
        assert crypto.is_encrypted(raw)

    def test_resaving_does_not_double_encrypt(self):
        cfg = _config()
        stored = cfg.api_key
        cfg.name = 'renamed'
        cfg.save()
        assert cfg.api_key == stored
        assert cfg.get_api_key() == SECRET

    def test_provider_receives_the_decrypted_key(self):
        provider = ProviderFactory.create(_config())
        assert provider.api_key == SECRET


class TestProfileSelection:
    def test_own_profile_wins_over_default(self):
        default = _config(use_for='all', name='d')
        writing = _config(use_for='writing', name='w')
        assert llm.resolve_configs('writing') == [writing]
        assert llm.resolve_configs('default') == [default]

    def test_profile_without_rows_falls_back_to_default(self):
        default = _config(use_for='all', name='d')
        assert llm.resolve_configs('speaking') == [default]

    def test_inactive_and_stt_rows_are_not_llm_candidates(self):
        _config(use_for='writing', is_active=False, name='off')
        _config(use_for='writing', name='stt', extra_options={'purpose': 'stt'})
        default = _config(use_for='all', name='d')
        assert llm.resolve_configs('writing') == [default]

    def test_priority_orders_candidates(self):
        second = _config(use_for='writing', priority=5, name='b')
        first = _config(use_for='writing', priority=1, name='a')
        assert llm.resolve_configs('writing') == [first, second]

    def test_no_rows_uses_settings_backend(self, settings, monkeypatch):
        settings.LLM_BACKEND = 'claude_cli'
        seen = {}

        class FakeCli:
            def complete(self, prompt, **kw):
                seen['prompt'] = prompt
                return 'cli-reply'

        monkeypatch.setattr(llm, 'ClaudeCLIProvider', lambda model: FakeCli())
        assert llm.generate('hi', profile='writing') == 'cli-reply'
        assert seen['prompt'] == 'hi'

    def test_failing_provider_falls_through_to_next(self, http):
        _config(use_for='writing', priority=1, name='a')
        _config(use_for='writing', priority=2, name='b', provider='ollama', key='')
        http.queue(500, {})
        http.queue(200, {'message': {'content': 'from-ollama'}})
        assert llm.generate('hi', profile='writing') == 'from-ollama'

    def test_first_error_is_raised_when_all_fail(self, http):
        _config(use_for='writing', priority=1, name='a')
        _config(use_for='writing', priority=2, name='b', provider='ollama', key='')
        http.queue(429, {})
        http.queue(401, {'error': 'bad'})
        with pytest.raises(TransientProviderError):
            llm.generate('hi', profile='writing')


class TestHttpProviders:
    def test_openai_request_and_reply(self, http):
        http.queue(200, {'choices': [{'message': {'content': '{"a": 1}'}}]})
        provider = ProviderFactory.create(_config(base_url='https://api.groq.com/openai/v1/'))
        out = provider.complete('p', system='s', json_schema={'type': 'object'})
        assert out == '{"a": 1}'
        call = http.calls[0]
        assert call['url'] == 'https://api.groq.com/openai/v1/chat/completions'
        assert call['headers']['Authorization'] == f'Bearer {SECRET}'
        assert call['json']['response_format'] == {'type': 'json_object'}
        assert call['json']['messages'][-1] == {'role': 'user', 'content': 'p'}

    def test_gemini_request_and_reply(self, http):
        http.queue(200, {'candidates': [{'content': {'parts': [{'text': 'he'}, {'text': 'llo'}]}}]})
        provider = ProviderFactory.create(_config(provider='gemini', model='gemini-2.0-flash'))
        assert provider.complete('p', system='s') == 'hello'
        call = http.calls[0]
        assert call['url'].endswith('/models/gemini-2.0-flash:generateContent')
        assert call['headers']['x-goog-api-key'] == SECRET
        assert call['json']['systemInstruction'] == {'parts': [{'text': 's'}]}

    def test_ollama_request_and_reply(self, http):
        http.queue(200, {'message': {'content': 'ok'}})
        provider = ProviderFactory.create(
            _config(provider='ollama', key='', base_url='http://gpu:11434')
        )
        assert provider.complete('p', json_schema={'type': 'object'}) == 'ok'
        call = http.calls[0]
        assert call['url'] == 'http://gpu:11434/api/chat'
        assert call['json']['format'] == 'json'

    @pytest.mark.parametrize('status', [429, 500, 503])
    def test_transient_statuses(self, http, status):
        http.queue(status, {})
        with pytest.raises(TransientProviderError):
            ProviderFactory.create(_config()).complete('p')

    def test_timeout_is_transient(self, http):
        http.queue(0, httpx.ReadTimeout('slow'))
        with pytest.raises(TransientProviderError):
            ProviderFactory.create(_config()).complete('p')

    def test_client_error_is_not_transient_and_hides_the_key(self, http):
        http.queue(401, {'error': f'Incorrect API key provided: {SECRET}'})
        with pytest.raises(RuntimeError) as exc:
            ProviderFactory.create(_config()).complete('p')
        assert not isinstance(exc.value, TransientProviderError)
        assert SECRET not in str(exc.value)

    def test_malformed_reply_raises(self, http):
        http.queue(200, {'unexpected': True})
        with pytest.raises(RuntimeError, match='Unexpected OpenAI'):
            ProviderFactory.create(_config()).complete('p')


class TestConnectionTest:
    def test_success(self, http):
        http.queue(200, {'choices': [{'message': {'content': 'ok'}}]})
        result = llm.test_connection(_config())
        assert result['ok'] is True
        assert result['error'] == ''

    def test_failure_never_echoes_the_key(self, http):
        http.queue(401, {'error': f'bad key {SECRET}'})
        result = llm.test_connection(_config())
        assert result['ok'] is False
        assert SECRET not in result['error']

    def test_transient_failure_is_reported_not_raised(self, http):
        http.queue(503, {})
        result = llm.test_connection(_config())
        assert result['ok'] is False
        assert 'Temporary' in result['error']

    def test_undecryptable_key_is_reported(self, settings):
        cfg = _config()
        settings.SECRET_KEY = 'rotated-secret-key'
        result = llm.test_connection(AIProviderConfig.objects.get(pk=cfg.pk))
        assert result['ok'] is False
        assert 'cannot be decrypted' in result['error']

"""C07 — admin-configured AI provider base URLs must not reach internal addresses (SSRF).

AIProviderConfig.base_url is chosen in the admin panel / staff API and the backend
then POSTs prompts (and the provider API key) to it, so it gets the same rules as
outgoing webhooks: validated on save and again, with the address pinned, at call time.
"""

import json
import socket
from unittest.mock import patch

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.catalog.admin import AIProviderConfigForm
from apps.catalog.models import AIProviderConfig
from apps.catalog.services.ai_providers import ProviderFactory
from apps.intelligence import llm, stt

pytestmark = [pytest.mark.security, pytest.mark.django_db]

PUBLIC_IP = '93.184.215.14'
SECRET = 'fake-provider-key-5555'  # obviously fake, low entropy (gitleaks)
GETADDRINFO = 'apps.webhooks.egress.socket.getaddrinfo'


def _addrinfo(*ips):
    def fake(host, port, *args, **kwargs):
        return [
            (
                socket.AF_INET6 if ':' in ip else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                '',
                (ip, port or 443),
            )
            for ip in ips
        ]

    return fake


INTERNAL_IPS = [
    '127.0.0.1',
    '10.0.0.7',
    '172.17.0.1',  # docker bridge
    '192.168.1.1',
    '169.254.169.254',  # cloud metadata
    '100.100.100.200',  # CGNAT-hosted metadata
    '::1',
    'fd00:ec2::254',
    '::ffff:169.254.169.254',
]

INTERNAL_URLS = [
    'https://169.254.169.254/latest/meta-data',
    'https://127.0.0.1:6379',
    'https://[::1]/v1',
    'https://2130706433/v1',  # decimal 127.0.0.1
    'https://localhost:11434',
    'https://metadata.google.internal/computeMetadata/v1',
    'https://redis.internal/v1',
    'http://api.example.com/v1',  # plain http outside DEBUG
    'ftp://api.example.com/v1',
    'https://user:pw@api.example.com/v1',
]


class FakeHttp:
    """Replaces httpx.post/get and records the calls (a call here = a request left the box)."""

    def __init__(self, monkeypatch, reply=None):
        self.calls = []
        self.reply = reply or (200, {'choices': [{'message': {'content': 'ok'}}]})
        monkeypatch.setattr(httpx, 'post', self._send)
        monkeypatch.setattr(httpx, 'get', self._send)

    def _send(self, url, **kwargs):
        self.calls.append({'url': url, **kwargs})
        status, body = self.reply
        headers = body.pop('_headers', {}) if isinstance(body, dict) else {}
        return httpx.Response(
            status, json=body, headers=headers, request=httpx.Request('POST', url)
        )


@pytest.fixture
def http(monkeypatch):
    return FakeHttp(monkeypatch)


def _config(base_url, provider='openai', **kw):
    """Create a row, bypassing save-time validation (legacy rows / raw SQL / migrations)."""
    with patch(GETADDRINFO, _addrinfo(PUBLIC_IP)):
        row = AIProviderConfig.objects.create(
            name=kw.pop('name', 'p'),
            provider=provider,
            model='m1',
            api_key='' if provider == 'ollama' else SECRET,
            base_url='https://api.example.com/v1',
            **kw,
        )
    AIProviderConfig.objects.filter(pk=row.pk).update(base_url=base_url)
    row.refresh_from_db()
    return row


# ─── Save time ───────────────────────────────────────────────────────────────


class TestSaveTime:
    @pytest.mark.parametrize('url', INTERNAL_URLS)
    def test_model_save_rejects_internal_url(self, url):
        with patch(GETADDRINFO, _addrinfo(PUBLIC_IP)), pytest.raises(ValidationError):
            AIProviderConfig.objects.create(
                name='evil', provider='openai', model='m', api_key=SECRET, base_url=url
            )
        assert not AIProviderConfig.objects.exists()

    @pytest.mark.parametrize('ip', INTERNAL_IPS)
    def test_model_save_rejects_host_resolving_to_internal_ip(self, ip):
        with patch(GETADDRINFO, _addrinfo(ip)), pytest.raises(ValidationError):
            AIProviderConfig.objects.create(
                name='evil',
                provider='openai',
                model='m',
                api_key=SECRET,
                base_url='https://llm.example.com/v1',
            )

    def test_update_to_internal_url_is_rejected(self):
        row = _config('https://api.example.com/v1')
        row.base_url = 'https://169.254.169.254/'
        with pytest.raises(ValidationError):
            row.save()

    def test_public_https_and_empty_are_accepted(self):
        with patch(GETADDRINFO, _addrinfo(PUBLIC_IP)):
            AIProviderConfig.objects.create(
                name='a', provider='openai', model='m', api_key=SECRET, base_url=''
            )
            AIProviderConfig.objects.create(
                name='b',
                provider='openai',
                model='m',
                api_key=SECRET,
                base_url='https://api.groq.com/openai/v1',
            )
        assert AIProviderConfig.objects.count() == 2

    def test_admin_form_rejects_internal_url(self):
        form = AIProviderConfigForm(
            data={
                'name': 'evil',
                'provider': 'openai',
                'model': 'm',
                'use_for': 'all',
                'priority': 1,
                'is_active': True,
                'api_key': SECRET,
                'base_url': 'https://169.254.169.254/latest',
                'extra_options': '{}',
            }
        )
        assert not form.is_valid()
        assert 'base_url' in form.errors

    @pytest.mark.parametrize('url', ['https://169.254.169.254/x', 'https://localhost:6379'])
    def test_staff_api_rejects_internal_url(self, url):
        staff = get_user_model().objects.create_user(
            username='staff-c07', email='s@e.com', password='pass123', is_staff=True
        )
        client = Client()
        client.force_login(staff)
        payload = {'name': 'evil', 'provider': 'openai', 'model': 'm', 'api_key': SECRET}
        resp = client.post(
            reverse('intelligence:ai-providers'),
            data=json.dumps({**payload, 'base_url': url}),
            content_type='application/json',
        )
        assert resp.status_code == 400, resp.content
        assert 'base_url' in resp.json() or 'base_url' in json.dumps(resp.json())
        assert not AIProviderConfig.objects.exists()


# ─── Call time ───────────────────────────────────────────────────────────────


class TestCallTime:
    @pytest.mark.parametrize(
        'provider,url',
        [
            ('openai', 'https://169.254.169.254/latest'),
            ('gemini', 'https://127.0.0.1:8000'),
            ('ollama', 'http://localhost:11434'),
            ('ollama', 'http://172.17.0.1:11434'),
        ],
    )
    def test_legacy_row_with_internal_url_never_sends(self, http, provider, url):
        provider_obj = ProviderFactory.create(_config(url, provider=provider))
        with pytest.raises(RuntimeError):
            provider_obj.complete('p')
        assert http.calls == []

    def test_ollama_parse_text_block_never_sends(self, http):
        provider_obj = ProviderFactory.create(_config('http://localhost:11434', provider='ollama'))
        assert 'error' in provider_obj.parse_text_block('text')
        assert http.calls == []

    def test_ollama_default_localhost_is_blocked_unless_allowlisted(self, http):
        provider_obj = ProviderFactory.create(_config('', provider='ollama'))
        with pytest.raises(RuntimeError):
            provider_obj.complete('p')
        assert http.calls == []

    def test_dns_rebinding_to_metadata_is_blocked_at_call_time(self, http):
        row = _config('https://llm.example.com/v1')
        with patch(GETADDRINFO, _addrinfo('169.254.169.254')):
            with pytest.raises(RuntimeError):
                ProviderFactory.create(row).complete('p')
        assert http.calls == []

    def test_stt_transcribe_and_probe_never_send(self, http):
        row = _config('https://10.0.0.7/v1', extra_options={'purpose': 'stt'})
        with pytest.raises(RuntimeError):
            stt.transcribe(row, b'RIFF', 'a.wav')
        with pytest.raises(RuntimeError):
            stt.check_stt(row)
        assert http.calls == []

    def test_connection_test_reports_block_without_sending(self, http):
        result = llm.test_connection(_config('https://169.254.169.254/'))
        assert result['ok'] is False
        assert http.calls == []

    def test_request_is_pinned_to_the_checked_address(self, http):
        row = _config('https://llm.example.com/v1')
        with patch(GETADDRINFO, _addrinfo(PUBLIC_IP)):
            assert ProviderFactory.create(row).complete('p') == 'ok'
        call = http.calls[0]
        assert call['url'] == f'https://{PUBLIC_IP}/v1/chat/completions'
        assert call['headers']['Host'] == 'llm.example.com'
        assert call['extensions'] == {'sni_hostname': 'llm.example.com'}
        assert call['follow_redirects'] is False

    def test_redirect_is_not_followed(self, monkeypatch):
        fake = FakeHttp(
            monkeypatch, reply=(302, {'_headers': {'Location': 'http://169.254.169.254/'}})
        )
        row = _config('https://llm.example.com/v1')
        with patch(GETADDRINFO, _addrinfo(PUBLIC_IP)), pytest.raises(RuntimeError):
            ProviderFactory.create(row).complete('p')
        assert len(fake.calls) == 1


# ─── Operator allowlist (self-hosted Ollama / vLLM on the private network) ──


class TestOperatorAllowlist:
    @pytest.fixture(autouse=True)
    def _allowlist(self, settings):
        settings.AI_PROVIDER_PRIVATE_HOSTS = ['ollama:11434']

    def test_allowlisted_host_and_port_is_accepted_and_sent_as_is(self, http):
        http.reply = (200, {'message': {'content': 'ok'}})
        with patch(GETADDRINFO, _addrinfo('172.18.0.5')):
            row = AIProviderConfig.objects.create(
                name='o', provider='ollama', model='llama3', base_url='http://ollama:11434'
            )
            assert ProviderFactory.create(row).complete('p') == 'ok'
        assert http.calls[0]['url'] == 'http://ollama:11434/api/chat'
        assert http.calls[0]['follow_redirects'] is False

    @pytest.mark.parametrize(
        'url', ['http://ollama:6379', 'http://localhost:11434', 'http://evil-ollama:11434']
    )
    def test_other_ports_and_hosts_stay_blocked(self, url):
        with patch(GETADDRINFO, _addrinfo('172.18.0.5')), pytest.raises(ValidationError):
            AIProviderConfig.objects.create(name='o', provider='ollama', model='l', base_url=url)

    def test_credentials_still_rejected_for_allowlisted_host(self):
        with pytest.raises(ValidationError):
            AIProviderConfig.objects.create(
                name='o', provider='ollama', model='l', base_url='http://u:p@ollama:11434'
            )

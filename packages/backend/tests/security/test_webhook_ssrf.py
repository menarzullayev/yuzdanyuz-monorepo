"""C07 — outgoing webhooks must not reach internal addresses (SSRF)."""

import socket
from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from apps.webhooks import egress
from apps.webhooks.models import WebhookDelivery, WebhookEndpoint
from apps.webhooks.tasks import RESPONSE_BODY_MAX_CHARS, deliver_webhook
from core.tenant import tenant_context

pytestmark = pytest.mark.security

PUBLIC_IP = '93.184.215.14'


def _addrinfo(*ips):
    def fake(host, port, *args, **kwargs):
        out = []
        for ip in ips:
            family = socket.AF_INET6 if ':' in ip else socket.AF_INET
            out.append((family, socket.SOCK_STREAM, 6, '', (ip, port or 443)))
        return out

    return fake


BLOCKED_IPS = [
    '127.0.0.1',
    '10.1.2.3',
    '172.16.0.5',
    '192.168.1.1',
    '169.254.169.254',  # cloud metadata
    '100.64.0.1',  # CGNAT
    '100.100.100.200',  # CGNAT-hosted metadata
    '0.0.0.0',
    '224.0.0.1',
    '::1',
    'fe80::1',
    'fd00:ec2::254',  # IPv6 metadata (ULA)
    '::ffff:127.0.0.1',  # IPv4-mapped loopback
    '::ffff:169.254.169.254',
    '64:ff9b::a9fe:a9fe',  # NAT64 of 169.254.169.254
    '2002:a9fe:a9fe::1',  # 6to4 of 169.254.169.254
]


class TestUrlValidation:
    @pytest.mark.parametrize('ip', BLOCKED_IPS)
    def test_hostname_resolving_to_internal_ip_is_rejected(self, ip):
        with patch('apps.webhooks.egress.socket.getaddrinfo', _addrinfo(ip)):
            with pytest.raises(ValidationError):
                egress.validate_webhook_url('https://hooks.example.com/x')

    @pytest.mark.parametrize(
        'url',
        [
            'https://127.0.0.1/x',
            'https://[::1]/x',
            'https://169.254.169.254/latest/meta-data/',
            'https://2130706433/x',  # decimal 127.0.0.1
            'https://localhost/x',
            'https://metadata.google.internal/x',
            'ftp://example.com/x',
            'file:///etc/passwd',
            'https://user:pw@example.com/x',
            'https:///nohost',
        ],
    )
    def test_bad_urls_are_rejected_without_dns(self, url):
        with patch('apps.webhooks.egress.socket.getaddrinfo', _addrinfo(PUBLIC_IP)):
            with pytest.raises(ValidationError):
                egress.validate_webhook_url(url)

    def test_mixed_answer_with_one_private_address_is_rejected(self):
        with patch('apps.webhooks.egress.socket.getaddrinfo', _addrinfo(PUBLIC_IP, '10.0.0.1')):
            with pytest.raises(ValidationError):
                egress.validate_webhook_url('https://hooks.example.com/x')

    @override_settings(DEBUG=False)
    def test_plain_http_is_rejected_in_production(self):
        with patch('apps.webhooks.egress.socket.getaddrinfo', _addrinfo(PUBLIC_IP)):
            with pytest.raises(ValidationError):
                egress.validate_webhook_url('http://hooks.example.com/x')

    @override_settings(DEBUG=True)
    def test_plain_http_is_allowed_in_debug(self):
        with patch('apps.webhooks.egress.socket.getaddrinfo', _addrinfo(PUBLIC_IP)):
            egress.validate_webhook_url('http://hooks.example.com/x')

    def test_public_https_url_is_accepted(self):
        with patch('apps.webhooks.egress.socket.getaddrinfo', _addrinfo(PUBLIC_IP)):
            egress.validate_webhook_url('https://hooks.example.com/x')

    def test_endpoint_with_internal_url_cannot_be_saved(self, org):
        with tenant_context(org), pytest.raises(ValidationError):
            WebhookEndpoint.objects.create(
                organization=org, name='evil', url='https://169.254.169.254/latest/'
            )


def _endpoint(org, url='https://hooks.example.com/x'):
    with patch('apps.webhooks.egress.socket.getaddrinfo', _addrinfo(PUBLIC_IP)):
        with tenant_context(org):
            return WebhookEndpoint.objects.create(
                organization=org, name='ep', url=url, events=['exam.submitted']
            )


def _delivery(endpoint):
    return WebhookDelivery.objects.create(
        endpoint=endpoint, event='exam.submitted', payload={'a': 1}
    )


@pytest.mark.django_db
class TestDeliveryTime:
    def test_dns_rebinding_to_metadata_is_blocked_at_send_time(self, org):
        delivery = _delivery(_endpoint(org))
        with (
            patch('apps.webhooks.egress.socket.getaddrinfo', _addrinfo('169.254.169.254')),
            patch('urllib3.connectionpool.HTTPConnectionPool.urlopen') as urlopen,
            patch('requests.post') as rpost,
        ):
            deliver_webhook.apply(args=[str(delivery.id)])
        urlopen.assert_not_called()
        rpost.assert_not_called()
        delivery.refresh_from_db()
        assert delivery.status == WebhookDelivery.Status.EXHAUSTED
        assert delivery.response_status is None

    def test_request_is_pinned_to_the_checked_ip_and_never_redirects(self, org):
        delivery = _delivery(_endpoint(org))
        resp = MagicMock(status=200)
        resp.read.return_value = b'ok'
        with (
            patch('apps.webhooks.egress.socket.getaddrinfo', _addrinfo(PUBLIC_IP)),
            patch(
                'urllib3.connectionpool.HTTPConnectionPool.urlopen',
                autospec=True,
                return_value=resp,
            ) as urlopen,
        ):
            deliver_webhook.apply(args=[str(delivery.id)])
        pool = urlopen.call_args.args[0]
        kwargs = urlopen.call_args.kwargs
        assert pool.host == PUBLIC_IP
        assert pool.conn_kw.get('server_hostname') == 'hooks.example.com'
        assert kwargs['redirect'] is False
        assert kwargs['headers']['Host'] == 'hooks.example.com'
        delivery.refresh_from_db()
        assert delivery.status == WebhookDelivery.Status.SENT

    def test_large_response_body_is_not_read_or_stored(self, org):
        delivery = _delivery(_endpoint(org))
        resp = MagicMock(status=500)
        resp.read.side_effect = lambda amt=None: b'x' * (amt if amt is not None else 10**7)
        with (
            patch('apps.webhooks.egress.socket.getaddrinfo', _addrinfo(PUBLIC_IP)),
            patch('urllib3.connectionpool.HTTPConnectionPool.urlopen', return_value=resp),
            patch('apps.webhooks.tasks.deliver_webhook.retry', side_effect=RuntimeError),
            pytest.raises(RuntimeError),  # 5xx goes down the retry path
        ):
            deliver_webhook.apply(args=[str(delivery.id)])
        amt = resp.read.call_args.kwargs['amt']
        assert amt is not None and amt <= 4096
        delivery.refresh_from_db()
        assert len(delivery.response_body) <= RESPONSE_BODY_MAX_CHARS <= 512

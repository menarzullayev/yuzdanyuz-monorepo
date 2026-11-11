"""Unit tests for core.utils.client_ip.get_client_ip."""

import pytest
from django.test import RequestFactory

from core.utils.client_ip import UNKNOWN_IP, get_client_ip

rf = RequestFactory()


def _req(**meta):
    return rf.get('/', **meta)


@pytest.mark.unit
class TestUntrustedPeer:
    @pytest.fixture(autouse=True)
    def _no_trust(self, settings):
        settings.TRUSTED_PROXY_CIDRS = []

    def test_plain_remote_addr(self):
        assert get_client_ip(_req(REMOTE_ADDR='203.0.113.9')) == '203.0.113.9'

    def test_xff_ignored(self):
        r = _req(REMOTE_ADDR='203.0.113.9', HTTP_X_FORWARDED_FOR='1.1.1.1')
        assert get_client_ip(r) == '203.0.113.9'

    def test_cf_connecting_ip_ignored(self):
        r = _req(REMOTE_ADDR='203.0.113.9', HTTP_CF_CONNECTING_IP='1.1.1.1')
        assert get_client_ip(r) == '203.0.113.9'

    def test_peer_outside_trusted_list_ignored(self, settings):
        settings.TRUSTED_PROXY_CIDRS = ['10.0.0.0/8']
        r = _req(REMOTE_ADDR='203.0.113.9', HTTP_CF_CONNECTING_IP='1.1.1.1')
        assert get_client_ip(r) == '203.0.113.9'

    def test_missing_or_garbage_remote_addr(self):
        r = _req()
        r.META.pop('REMOTE_ADDR', None)
        assert get_client_ip(r) == UNKNOWN_IP
        assert get_client_ip(_req(REMOTE_ADDR='not-an-ip')) == UNKNOWN_IP


@pytest.mark.unit
class TestTrustedPeer:
    @pytest.fixture(autouse=True)
    def _trust(self, settings):
        settings.TRUSTED_PROXY_CIDRS = ['10.0.0.0/8', '173.245.48.0/20', '2400:cb00::/32']

    def test_cf_connecting_ip_used_when_whole_chain_trusted(self):
        # ingress-nginx default: XFF replaced by the Cloudflare edge address.
        r = _req(
            REMOTE_ADDR='10.1.1.1',
            HTTP_CF_CONNECTING_IP='198.51.100.4',
            HTTP_X_FORWARDED_FOR='173.245.48.1',
        )
        assert get_client_ip(r) == '198.51.100.4'

    def test_forged_cf_header_on_direct_origin_hit_ignored(self):
        # Bypassing Cloudflare: ingress appends the attacker's real address.
        r = _req(
            REMOTE_ADDR='10.1.1.1',
            HTTP_CF_CONNECTING_IP='1.1.1.1',
            HTTP_X_FORWARDED_FOR='203.0.113.66',
        )
        assert get_client_ip(r) == '203.0.113.66'

    def test_xff_client_agrees_with_cf_header(self):
        r = _req(
            REMOTE_ADDR='10.1.1.1',
            HTTP_CF_CONNECTING_IP='198.51.100.4',
            HTTP_X_FORWARDED_FOR='6.6.6.6, 198.51.100.4, 173.245.48.1',
        )
        assert get_client_ip(r) == '198.51.100.4'

    def test_invalid_cf_header_falls_back_to_peer(self):
        r = _req(REMOTE_ADDR='10.1.1.1', HTTP_CF_CONNECTING_IP='garbage')
        assert get_client_ip(r) == '10.1.1.1'

    def test_xff_rightmost_untrusted_skips_proxy_chain(self):
        r = _req(
            REMOTE_ADDR='10.1.1.1',
            HTTP_X_FORWARDED_FOR='6.6.6.6, 7.7.7.7, 198.51.100.4, 173.245.48.1, 10.2.2.2',
        )
        assert get_client_ip(r) == '198.51.100.4'

    def test_xff_all_trusted_falls_back_to_peer(self):
        r = _req(REMOTE_ADDR='10.1.1.1', HTTP_X_FORWARDED_FOR='10.3.3.3')
        assert get_client_ip(r) == '10.1.1.1'

    def test_xff_malformed_hop_stops_walk(self):
        r = _req(REMOTE_ADDR='10.1.1.1', HTTP_X_FORWARDED_FOR='6.6.6.6, bogus, 10.3.3.3')
        assert get_client_ip(r) == '10.1.1.1'

    def test_no_headers_uses_peer(self):
        assert get_client_ip(_req(REMOTE_ADDR='10.1.1.1')) == '10.1.1.1'

    def test_ipv6_and_ports(self):
        r = _req(REMOTE_ADDR='2400:cb00::1', HTTP_X_FORWARDED_FOR='[2001:db8::7]:4431')
        assert get_client_ip(r) == '2001:db8::7'
        r = _req(REMOTE_ADDR='10.1.1.1', HTTP_X_FORWARDED_FOR='198.51.100.4:5555')
        assert get_client_ip(r) == '198.51.100.4'

    def test_ipv4_mapped_peer_is_trusted(self):
        r = _req(REMOTE_ADDR='::ffff:10.1.1.1', HTTP_CF_CONNECTING_IP='198.51.100.4')
        assert get_client_ip(r) == '198.51.100.4'


@pytest.mark.unit
def test_string_setting_and_invalid_cidr_tolerated(settings):
    settings.TRUSTED_PROXY_CIDRS = '10.0.0.0/8, not-a-cidr'
    r = _req(REMOTE_ADDR='10.1.1.1', HTTP_CF_CONNECTING_IP='198.51.100.4')
    assert get_client_ip(r) == '198.51.100.4'

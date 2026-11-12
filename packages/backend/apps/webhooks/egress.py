"""Outbound HTTP for tenant-configured webhook URLs, hardened against SSRF.

A webhook URL is chosen by a tenant, so the request must never reach the
platform's own network: loopback, RFC 1918, link-local (cloud metadata),
CGNAT, ULA, multicast and every IPv6 form that embeds such an IPv4 address.

The host is resolved once, every resolved address must be public, and the
connection is made to that exact address (TLS still verifies the hostname),
so a DNS answer that changes between the check and the request cannot
redirect it. Redirects are never followed and only a small prefix of the
response body is read.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import certifi
import urllib3
from django.conf import settings
from django.core.exceptions import ValidationError

_IP = ipaddress.IPv4Address | ipaddress.IPv6Address

BLOCKED_HOSTNAMES = frozenset(
    {
        'localhost',
        'localhost.localdomain',
        'metadata',
        'metadata.google.internal',
        'instance-data',
    }
)
BLOCKED_SUFFIXES = ('.localhost', '.local', '.internal', '.localdomain', '.home.arpa')

_NAT64 = ipaddress.ip_network('64:ff9b::/96')


class BlockedDestination(Exception):
    """The URL points at a destination webhooks may never reach."""


class EgressError(Exception):
    """Network failure while delivering (DNS, connect, TLS, timeout)."""


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str
    port: int
    path: str


@dataclass(frozen=True)
class EgressResponse:
    status_code: int
    text: str


def is_public_ip(ip: _IP) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if embedded is None and ip in _NAT64:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            return is_public_ip(embedded)
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def _allowed_schemes() -> tuple[str, ...]:
    return ('https', 'http') if settings.DEBUG else ('https',)


def parse_target(url: str) -> Target:
    """Static checks that need no DNS. Raises BlockedDestination."""
    try:
        parts = urlsplit((url or '').strip())
        port = parts.port
    except ValueError as exc:
        raise BlockedDestination('malformed URL') from exc
    scheme = parts.scheme.lower()
    if scheme not in _allowed_schemes():
        raise BlockedDestination(f'scheme must be one of {", ".join(_allowed_schemes())}')
    if parts.username is not None or parts.password is not None:
        raise BlockedDestination('credentials in the URL are not allowed')
    host = (parts.hostname or '').rstrip('.').lower()
    if not host:
        raise BlockedDestination('URL has no host')
    if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
        raise BlockedDestination('internal host name')
    literal = _ip_literal(host)
    if literal is not None and not is_public_ip(literal):
        raise BlockedDestination('non-public address')
    path = parts.path or '/'
    if parts.query:
        path = f'{path}?{parts.query}'
    return Target(scheme, host, port or (443 if scheme == 'https' else 80), path)


def _ip_literal(host: str) -> _IP | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    # Resolvers also honour the legacy numeric forms (2130706433, 0x7f.1, 127.1);
    # inet_aton accepts exactly those and rejects real host names.
    try:
        return ipaddress.IPv4Address(socket.inet_aton(host))
    except OSError:
        return None


def resolve_public(target: Target) -> list[str]:
    """Resolve the host; every answer must be public. Raises BlockedDestination/EgressError."""
    try:
        infos = socket.getaddrinfo(target.host, target.port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise EgressError(f'DNS lookup failed: {exc}') from exc
    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip = ipaddress.ip_address(str(sockaddr[0]).split('%', 1)[0])
        if not is_public_ip(ip):
            raise BlockedDestination('host resolves to a non-public address')
        if str(ip) not in addresses:
            addresses.append(str(ip))
    if not addresses:
        raise EgressError('DNS lookup returned no address')
    return addresses


def validate_webhook_url(url: str) -> None:
    """Model/admin validation. DNS failure is tolerated here; send time re-checks."""
    try:
        target = parse_target(url)
        if _ip_literal(target.host) is None:
            try:
                resolve_public(target)
            except EgressError:
                pass
    except BlockedDestination as exc:
        raise ValidationError(f'Webhook URL rejected: {exc}.', code='webhook_url') from exc


def _host_header(target: Target) -> str:
    host = f'[{target.host}]' if ':' in target.host else target.host
    default = 443 if target.scheme == 'https' else 80
    return host if target.port == default else f'{host}:{target.port}'


def safe_post(
    url: str,
    *,
    body: bytes,
    headers: dict[str, str],
    timeout: float,
    max_body_bytes: int,
) -> EgressResponse:
    target = parse_target(url)
    ip = resolve_public(target)[0]
    request_headers = {**headers, 'Host': _host_header(target)}
    pool_timeout = urllib3.Timeout(connect=timeout, read=timeout)
    if target.scheme == 'https':
        pool = urllib3.HTTPSConnectionPool(
            ip,
            port=target.port,
            timeout=pool_timeout,
            retries=False,
            maxsize=1,
            cert_reqs='CERT_REQUIRED',
            ca_certs=certifi.where(),
            assert_hostname=target.host,
            server_hostname=target.host,
        )
    else:
        pool = urllib3.HTTPConnectionPool(
            ip, port=target.port, timeout=pool_timeout, retries=False, maxsize=1
        )
    try:
        response = pool.urlopen(
            'POST',
            target.path,
            body=body,
            headers=request_headers,
            redirect=False,
            retries=False,
            preload_content=False,
        )
        try:
            raw = response.read(amt=max_body_bytes) or b''
        finally:
            response.release_conn()
    except urllib3.exceptions.HTTPError as exc:
        raise EgressError(str(exc)) from exc
    finally:
        pool.close()
    return EgressResponse(status_code=int(response.status), text=raw.decode('utf-8', 'replace'))

"""Single source of truth for the client IP address of a request.

Forwarding headers (``CF-Connecting-IP``, ``X-Forwarded-For``) are written by
whoever sent the request, so they are only believed when the immediate TCP
peer (``REMOTE_ADDR``) is one of our own proxies, listed in
``settings.TRUSTED_PROXY_CIDRS``. With the default empty list nothing is
trusted and the peer address is used as is.

When the peer is trusted, ``X-Forwarded-For`` is walked from the right,
skipping hops that are trusted proxies; the first untrusted hop is the
client. The left-most entries are whatever the client chose to send and are
never used.

``CF-Connecting-IP`` is used only when every hop in that chain is trusted,
i.e. the request provably came in through Cloudflare (whose ranges must then
be listed) and no untrusted hop is on record. A request that reaches the
origin directly carries its real address as an untrusted hop appended by
the ingress, so a forged ``CF-Connecting-IP`` on it is ignored.
"""

from __future__ import annotations

import ipaddress
import logging
from functools import lru_cache

from django.conf import settings

log = logging.getLogger(__name__)

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
_IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

UNKNOWN_IP = '0.0.0.0'


@lru_cache(maxsize=8)
def _parse_networks(cidrs: tuple[str, ...]) -> tuple[_IPNetwork, ...]:
    networks = []
    for cidr in cidrs:
        cidr = cidr.strip()
        if not cidr:
            continue
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            log.error('TRUSTED_PROXY_CIDRS: ignoring invalid entry %r', cidr)
    return tuple(networks)


def _trusted_networks() -> tuple[_IPNetwork, ...]:
    raw = getattr(settings, 'TRUSTED_PROXY_CIDRS', ()) or ()
    if isinstance(raw, str):
        raw = raw.split(',')
    return _parse_networks(tuple(raw))


def _parse_ip(value: str | None) -> _IPAddress | None:
    """Parse one address, tolerating ``host:port`` and ``[v6]:port`` forms."""
    if not value:
        return None
    value = value.strip()
    if value.startswith('['):
        value = value[1 : value.find(']')] if ']' in value else value[1:]
    elif value.count(':') == 1:
        value = value.split(':', 1)[0]
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return None
    # Normalise IPv4-mapped IPv6 (::ffff:1.2.3.4) so buckets and CIDRs match.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _is_trusted(ip: _IPAddress, networks: tuple[_IPNetwork, ...]) -> bool:
    return any(ip.version == net.version and ip in net for net in networks)


def get_client_ip(request) -> str:
    """Return the best-known client IP for ``request`` as a string."""
    meta = request.META
    peer = _parse_ip(meta.get('REMOTE_ADDR'))
    if peer is None:
        return UNKNOWN_IP

    networks = _trusted_networks()
    if not networks or not _is_trusted(peer, networks):
        return str(peer)

    xff = meta.get('HTTP_X_FORWARDED_FOR', '')
    for hop in reversed([h for h in xff.split(',') if h.strip()]):
        ip = _parse_ip(hop)
        if ip is None:
            # Malformed entry before reaching an untrusted hop: stop rather
            # than skip past it to attacker-controlled entries.
            return str(peer)
        if not _is_trusted(ip, networks):
            return str(ip)

    cf_ip = _parse_ip(meta.get('HTTP_CF_CONNECTING_IP'))
    if cf_ip is not None:
        return str(cf_ip)
    return str(peer)

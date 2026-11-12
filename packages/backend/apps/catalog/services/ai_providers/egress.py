"""SSRF guard for admin-configured AI provider base URLs (audit C07).

AIProviderConfig.base_url is typed into the admin panel, and the backend then POSTs
prompts and the provider API key to it. It follows the outgoing-webhook rules
(apps.webhooks.egress): HTTPS only outside DEBUG, no credentials in the URL, every
resolved address must be public, and the request is pinned to the address that was
checked (Host header + TLS SNI keep the real host name), so DNS rebinding between
the check and the request cannot redirect it.

Self-hosted models on the private network are the one exception, and only the
operator can grant it: settings.AI_PROVIDER_PRIVATE_HOSTS (`host:port` or `host`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.webhooks.egress import (
    BlockedDestination,
    EgressError,
    _host_header,
    _ip_literal,
    parse_target,
    resolve_public,
)

__all__ = ['BlockedDestination', 'EgressError', 'PinnedRequest', 'pin', 'validate_base_url']


@dataclass(frozen=True)
class PinnedRequest:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    extensions: dict[str, str] = field(default_factory=dict)


def _operator_allowed(url: str) -> bool:
    """True when the operator allow-listed this exact host (and port, if given)."""
    allowed = getattr(settings, 'AI_PROVIDER_PRIVATE_HOSTS', None) or []
    if not allowed:
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    scheme = parts.scheme.lower()
    host = (parts.hostname or '').rstrip('.').lower()
    if scheme not in ('http', 'https') or not host:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    port = port or (443 if scheme == 'https' else 80)
    return host in allowed or f'{host}:{port}' in allowed


def pin(url: str) -> PinnedRequest:
    """Check the URL and return how to send to it. Raises BlockedDestination / EgressError."""
    url = (url or '').strip()
    if _operator_allowed(url):
        return PinnedRequest(url)
    target = parse_target(url)
    ip = resolve_public(target)[0]
    ip_host = f'[{ip}]' if ':' in ip else ip
    default_port = 443 if target.scheme == 'https' else 80
    netloc = ip_host if target.port == default_port else f'{ip_host}:{target.port}'
    parts = urlsplit(url)
    pinned = urlunsplit((target.scheme, netloc, parts.path, parts.query, ''))
    extensions = {'sni_hostname': target.host} if target.scheme == 'https' else {}
    return PinnedRequest(pinned, {'Host': _host_header(target)}, extensions)


def validate_base_url(url: str) -> None:
    """Model/admin/API validation. Empty = provider default. DNS failure is tolerated here."""
    url = (url or '').strip()
    if not url or _operator_allowed(url):
        return
    try:
        target = parse_target(url)
        if _ip_literal(target.host) is None:
            try:
                resolve_public(target)
            except EgressError:
                pass
    except BlockedDestination as exc:
        raise ValidationError(
            {'base_url': f'Base URL rejected: {exc}.'}, code='ai_base_url'
        ) from exc

"""Shared HTTP helper for the REST-based providers (OpenAI-compatible, Gemini, Ollama).

Every request goes through egress.pin(): the admin-configured base URL is re-checked
against SSRF rules at call time and the connection is pinned to the checked address.
Redirects are never followed.
"""

import httpx

from .base import TransientProviderError
from .egress import BlockedDestination, EgressError, pin

DEFAULT_TIMEOUT = 120.0


class BlockedProviderURL(RuntimeError):
    """The provider URL points at a destination the backend may not reach (C07)."""


def _where(url: str) -> str:
    return url.split('?')[0]


def _send(method: str, url: str, *, headers: dict | None, timeout: float, **kwargs):
    try:
        target = pin(url)
    except BlockedDestination as exc:
        raise BlockedProviderURL(f'Blocked AI provider URL {_where(url)}: {exc}') from None
    except EgressError as exc:
        raise TransientProviderError(f'{exc}: {_where(url)}') from None
    send = httpx.post if method == 'POST' else httpx.get
    try:
        return send(
            target.url,
            headers={**(headers or {}), **target.headers},
            timeout=timeout,
            follow_redirects=False,
            extensions=target.extensions,
            **kwargs,
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise TransientProviderError(f'{type(exc).__name__}: {_where(url)}') from None


def post_json(
    url: str,
    *,
    json: dict,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    secret: str = '',
) -> dict:
    """POST and return the decoded JSON body.

    Timeouts, connection errors, 429 and 5xx raise TransientProviderError; other non-2xx
    responses raise RuntimeError. The API key never appears in an error message.
    """
    resp = _send('POST', url, headers=headers, timeout=timeout, json=json)
    return _decode(resp, url, secret)


def _decode(resp: httpx.Response, url: str, secret: str) -> dict:
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientProviderError(f'HTTP {resp.status_code} from {_where(url)}')
    if 300 <= resp.status_code < 400:
        raise RuntimeError(
            f'HTTP {resp.status_code} from {_where(url)}: redirects are not followed'
        )
    if resp.status_code >= 400:
        body = resp.text[:300]
        if secret:
            body = body.replace(secret, '***')
        raise RuntimeError(f'HTTP {resp.status_code} from {_where(url)}: {body}')
    return resp.json()


def post_multipart(
    url: str,
    *,
    data: dict,
    files: dict,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    secret: str = '',
) -> dict:
    """Multipart POST (audio upload); same error contract as post_json."""
    resp = _send('POST', url, headers=headers, timeout=timeout, data=data, files=files)
    return _decode(resp, url, secret)


def get_json(
    url: str, *, headers: dict | None = None, timeout: float = 30.0, secret: str = ''
) -> dict:
    resp = _send('GET', url, headers=headers, timeout=timeout)
    return _decode(resp, url, secret)

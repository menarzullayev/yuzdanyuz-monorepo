"""Shared HTTP helper for the REST-based providers (OpenAI-compatible, Gemini, Ollama)."""

import httpx

from .base import TransientProviderError

DEFAULT_TIMEOUT = 120.0


def _where(url: str) -> str:
    return url.split('?')[0]


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
    try:
        resp = httpx.post(url, json=json, headers=headers or {}, timeout=timeout)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise TransientProviderError(f'{type(exc).__name__}: {_where(url)}') from None
    return _decode(resp, url, secret)


def _decode(resp: httpx.Response, url: str, secret: str) -> dict:
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientProviderError(f'HTTP {resp.status_code} from {_where(url)}')
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
    try:
        resp = httpx.post(url, data=data, files=files, headers=headers or {}, timeout=timeout)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise TransientProviderError(f'{type(exc).__name__}: {_where(url)}') from None
    return _decode(resp, url, secret)


def get_json(
    url: str, *, headers: dict | None = None, timeout: float = 30.0, secret: str = ''
) -> dict:
    try:
        resp = httpx.get(url, headers=headers or {}, timeout=timeout)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise TransientProviderError(f'{type(exc).__name__}: {_where(url)}') from None
    return _decode(resp, url, secret)

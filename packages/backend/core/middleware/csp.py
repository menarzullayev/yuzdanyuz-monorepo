"""Content-Security-Policy for every Django response (audit C14).

* non-HTML (the JSON API, files, health): enforced `default-src 'none'`;
* the Django admin: enforced, scripts only from 'self' or with the per-request nonce;
* other HTML pages (index, Swagger/Redoc, allauth): they still load htmx, Alpine and
  Swagger UI from CDNs and Alpine needs eval, so the full policy is Report-Only there;
  frame-ancestors / object-src / base-uri are enforced on them anyway.

Templates put the nonce on inline scripts: <script nonce="{{ csp_nonce }}">.
CSP_REPORT_URI (optional) is added as report-uri to every policy.
"""

from __future__ import annotations

import secrets

from django.conf import settings

ENFORCED = 'Content-Security-Policy'
REPORT_ONLY = 'Content-Security-Policy-Report-Only'

API_POLICY = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
FRAMING_POLICY = "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"


def _join(directives: dict[str, list[str]]) -> str:
    return '; '.join(f'{k} {" ".join(v)}' for k, v in directives.items())


def admin_policy(nonce: str) -> str:
    return _join(
        {
            'default-src': ["'self'"],
            'script-src': ["'self'", f"'nonce-{nonce}'"],
            # admin templates use style="" attributes
            'style-src': ["'self'", "'unsafe-inline'"],
            'img-src': ["'self'", 'data:'],
            'font-src': ["'self'"],
            'connect-src': ["'self'"],
            'frame-ancestors': ["'none'"],
            'object-src': ["'none'"],
            'base-uri': ["'self'"],
            'form-action': ["'self'"],
        }
    )


def page_policy(nonce: str) -> str:
    cdns = ['https://unpkg.com', 'https://cdn.jsdelivr.net']
    return _join(
        {
            'default-src': ["'self'"],
            'script-src': ["'self'", f"'nonce-{nonce}'", *cdns],
            'style-src': ["'self'", "'unsafe-inline'", 'https://cdn.jsdelivr.net'],
            'img-src': ["'self'", 'data:', 'https:'],
            'font-src': ["'self'", 'data:'],
            'connect-src': ["'self'"],
            'frame-ancestors': ["'none'"],
            'object-src': ["'none'"],
            'base-uri': ["'self'"],
            'form-action': ["'self'", 'https://accounts.google.com'],
        }
    )


def _with_report(policy: str) -> str:
    uri = getattr(settings, 'CSP_REPORT_URI', '')
    return f'{policy}; report-uri {uri}' if uri else policy


class CSPMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(16)
        response = self.get_response(request)
        if ENFORCED in response:
            return response  # a view set its own policy
        nonce = request.csp_nonce
        if not response.get('Content-Type', '').startswith('text/html'):
            response[ENFORCED] = _with_report(API_POLICY)
        elif request.path_info.startswith(tuple(settings.CSP_ENFORCED_HTML_PREFIXES)):
            response[ENFORCED] = _with_report(admin_policy(nonce))
        else:
            response[ENFORCED] = _with_report(FRAMING_POLICY)
            response[REPORT_ONLY] = _with_report(page_policy(nonce))
        return response


def csp_nonce(request):
    """Template context processor: {{ csp_nonce }}."""
    return {'csp_nonce': getattr(request, 'csp_nonce', '')}

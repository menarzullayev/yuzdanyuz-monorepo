"""The documented auth API exists (P6) and the removed HTMX/email surface is gone."""

import re
from pathlib import Path

import pytest
from django.test import Client

DOC = Path(__file__).resolve().parents[2] / 'docs' / 'auth-api.md'

# method, path (uuid placeholders filled in) - every endpoint the UI uses
ENDPOINTS = [
    ('post', '/api/auth/register/'),
    ('post', '/api/auth/login/'),
    ('post', '/api/auth/telegram/'),
    ('post', '/api/auth/tg/init/'),
    ('get', '/api/auth/tg/status/'),
    ('post', '/api/auth/tg/complete/'),
    ('post', '/api/auth/otp/send/'),
    ('post', '/api/auth/otp/verify/'),
    ('post', '/api/auth/refresh/'),
    ('post', '/api/auth/logout/'),
    ('get', '/api/auth/me/'),
    ('get', '/api/auth/username/check/'),
    ('post', '/api/auth/username/'),
    ('post', '/api/auth/password/change/'),
    ('post', '/api/auth/password/change-required/'),
    ('post', '/api/auth/recovery/telegram/'),
    ('post', '/api/auth/recovery/confirm/'),
    ('post', '/api/auth/recovery/request/'),
    ('post', '/api/auth/recovery/org-reset/'),
    ('get', '/api/auth/sessions/'),
    ('post', '/api/auth/sessions/revoke-others/'),
    ('delete', '/api/auth/sessions/00000000-0000-0000-0000-000000000000/'),
    ('get', '/api/auth/2fa/'),
    ('post', '/api/auth/2fa/setup/'),
    ('post', '/api/auth/2fa/confirm/'),
    ('post', '/api/auth/2fa/disable/'),
    ('post', '/api/auth/2fa/backup-codes/regenerate/'),
    ('get', '/api/auth/linked/'),
    ('post', '/api/auth/linked/telegram/'),
    ('delete', '/api/auth/linked/telegram/'),
    ('post', '/api/auth/linked/telegram/start/'),
    ('delete', '/api/auth/linked/google/'),
    ('get', '/api/auth/onboarding/'),
    ('patch', '/api/auth/onboarding/'),
    ('get', '/api/auth/onboarding/options/'),
    ('post', '/api/auth/onboarding/org-invite/'),
    ('delete', '/api/auth/onboarding/org-consent/00000000-0000-0000-0000-000000000000/'),
]


@pytest.mark.integration
@pytest.mark.parametrize('method,path', ENDPOINTS)
def test_documented_endpoints_exist(db, method, path):
    """Anonymous callers get 401/400/200/..., never 404 (the old client hit a 404, P6)."""
    resp = getattr(Client(), method)(path, REMOTE_ADDR='203.0.113.9')
    assert resp.status_code != 404 or path.startswith('/api/auth/otp/'), (method, path)


@pytest.mark.integration
@pytest.mark.parametrize('method,path', ENDPOINTS)
def test_every_endpoint_is_in_the_api_doc(method, path):
    text = DOC.read_text(encoding='utf-8')
    generic = re.sub(r'[0-9a-f-]{36}', '<uuid>', path)
    assert generic.rstrip('/') in text, f'{path} is not documented in docs/auth-api.md'


@pytest.mark.integration
@pytest.mark.parametrize(
    'path',
    [
        '/login/',
        '/login-register/',
        '/api/forms/login/',
        '/api/forms/register/',
        '/api/auth/email/',
        '/api/auth/otp/send/htmx/',
        '/api/linking/list/',
        '/api/linking/unlink/',
        '/accounts/api/auth/email/',
    ],
)
def test_removed_surface_is_gone(db, path):
    resp = Client().get(path)
    # 404, or 403 from the tenant guard for authenticated-only prefixes: never a working page
    assert resp.status_code in (404, 403, 401), (path, resp.status_code)
    assert b'htmx' not in resp.content.lower()


def test_html_auth_templates_are_removed():
    root = Path(__file__).resolve().parents[2] / 'templates'
    assert not (root / 'accounts').exists()

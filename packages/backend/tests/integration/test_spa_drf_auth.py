"""The Next.js app signs in with the JWT cookie and sends no CSRF token.

DRF's default SessionAuthentication enforces Django's CSRF token for any user found
on the request, so every SPA POST to a DRF view (/api/exams/..., /api/billing/...)
was rejected with 403 "CSRF token missing". JWT-cookie requests are protected like
the /api/auth/ views instead: SameSite cookie plus a same-origin Origin check.
"""

import json

import pytest
from django.test import Client

from tests.integration._onboarding import complete_onboarding
from tests.integration.test_exam_api import _make_published_mock, _make_qv

PASSWORD = 'Str0ng-pass-phrase!'


@pytest.fixture
def spa(db, user, member):
    user.set_password(PASSWORD)
    user.save()
    complete_onboarding(user)
    c = Client(enforce_csrf_checks=True)
    resp = c.post(
        '/api/auth/login/',
        data=json.dumps({'username': user.username, 'password': PASSWORD}),
        content_type='application/json',
    )
    assert resp.status_code == 200, resp.content
    assert 'access_token' in c.cookies
    return c


@pytest.mark.integration
class TestSpaPostsToDrfViews:
    def test_jwt_cookie_post_without_csrf_token_works(self, spa, org, user, subject):
        mock = _make_published_mock(org, user, qvs=[_make_qv(org, subject)])
        resp = spa.post(f'/api/exams/mocks/{mock.id}/start/', HTTP_ORIGIN='http://testserver')
        assert resp.status_code == 201, resp.content

    def test_foreign_origin_is_refused(self, spa, org, user, subject):
        mock = _make_published_mock(org, user, qvs=[_make_qv(org, subject)])
        resp = spa.post(f'/api/exams/mocks/{mock.id}/start/', HTTP_ORIGIN='https://evil.example')
        assert resp.status_code == 403

    def test_session_login_still_needs_csrf_token(self, db, org, user, member, subject):
        """Django-session users (admin, HTMX pages) keep Django's CSRF protection."""
        complete_onboarding(user)
        mock = _make_published_mock(org, user, qvs=[_make_qv(org, subject)])
        c = Client(enforce_csrf_checks=True)
        c.force_login(user)
        resp = c.post(f'/api/exams/mocks/{mock.id}/start/')
        assert resp.status_code == 403

    def test_anonymous_gets_401(self, db, org, user, subject):
        """401 (not 403) makes the SPA client refresh an expired access cookie and retry."""
        mock = _make_published_mock(org, user, qvs=[_make_qv(org, subject)])
        resp = Client(enforce_csrf_checks=True).post(f'/api/exams/mocks/{mock.id}/start/')
        assert resp.status_code == 401

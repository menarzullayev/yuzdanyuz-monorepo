"""HTTP-level device sessions: refresh from the refresh cookie alone (P1),
revocation, the sessions API and cookie handling."""

import pytest
from django.test import Client

from apps.accounts.models import DeviceSession
from apps.accounts.services.token_service import create_token_pair

UA_CHROME = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36'
UA_PHONE = 'Mozilla/5.0 (Linux; Android 14) Chrome/120.0 Mobile Safari/537.36'


def _client_for(user, device='device-aaaaaaaaaaaaaaaa', *, access=True, ua=UA_CHROME, **kw):
    """A client carrying real cookies for a freshly created session."""
    from django.test import RequestFactory

    req = RequestFactory().get('/', HTTP_USER_AGENT=ua)
    access_token, refresh = create_token_pair(user, device, request=req, **kw)
    c = Client(HTTP_USER_AGENT=ua)
    if access:
        c.cookies['access_token'] = access_token
    c.cookies['refresh_token'] = refresh
    c.cookies['device_id'] = device
    return c, access_token, refresh


@pytest.mark.integration
class TestRefreshWithoutAccessCookie:
    def test_refresh_works_with_only_the_refresh_cookie(self, user):
        """P1: the access cookie expired (max_age 900); refresh must still work."""
        client, _, refresh = _client_for(user, access=False)

        resp = client.post('/api/auth/refresh/')

        assert resp.status_code == 200, resp.content
        assert resp.cookies['access_token'].value
        assert resp.cookies['refresh_token'].value != refresh

    def test_refreshed_access_cookie_authenticates(self, user):
        client, _, _ = _client_for(user, access=False)
        resp = client.post('/api/auth/refresh/')
        client.cookies['access_token'] = resp.cookies['access_token'].value
        assert client.get('/api/auth/sessions/').status_code == 200

    def test_refresh_without_cookie_is_401(self, db):
        assert Client().post('/api/auth/refresh/').status_code == 401

    def test_refresh_with_forged_cookie_is_401_and_clears_cookies(self, user):
        client, _, _ = _client_for(user, access=False)
        client.cookies['refresh_token'] = 'f' * 32 + '.forged'
        resp = client.post('/api/auth/refresh/')
        assert resp.status_code == 401
        assert resp.cookies['refresh_token'].value == ''

    def test_revoked_session_cannot_refresh(self, user):
        client, _, _ = _client_for(user, access=False)
        DeviceSession.objects.update(revoked_reason='x', revoked_at='2020-01-01T00:00:00Z')
        assert client.post('/api/auth/refresh/').status_code == 401


@pytest.mark.integration
class TestCookies:
    def test_refresh_cookie_path_and_flags(self, user):
        client, _, _ = _client_for(user, access=False)
        resp = client.post('/api/auth/refresh/')
        refresh = resp.cookies['refresh_token']
        assert refresh['path'] == '/api/auth/'
        assert refresh['httponly']
        assert refresh['max-age'] == 30 * 24 * 3600
        assert resp.cookies['device_id']['httponly']

    def test_logout_deletes_refresh_cookie_with_the_same_path(self, user):
        """P1b: delete_cookie must use the path the cookie was set with."""
        client, _, _ = _client_for(user)
        resp = client.post('/api/auth/logout/')
        assert resp.status_code == 200
        assert resp.cookies['refresh_token']['path'] == '/api/auth/'
        assert resp.cookies['refresh_token'].value == ''
        assert resp.cookies['access_token']['path'] == '/'

    def test_not_remembered_session_uses_browser_session_cookies(self, user):
        from django.http import HttpResponse
        from django.test import RequestFactory

        from apps.accounts.services.token_service import attach_session, issue_login

        request = RequestFactory().get('/')
        issued = issue_login(request, user, remember=False)
        response = HttpResponse()
        attach_session(response, issued, request)
        assert response.cookies['access_token']['max-age'] == ''
        assert response.cookies['refresh_token']['max-age'] == ''
        assert response.cookies['device_id']['max-age']  # device id stays persistent


@pytest.mark.integration
class TestLogout:
    def test_logout_revokes_the_session_server_side(self, user):
        client, access, refresh = _client_for(user)
        client.post('/api/auth/logout/')

        replay = Client()
        replay.cookies['refresh_token'] = refresh
        assert replay.post('/api/auth/refresh/').status_code == 401
        # and the still-unexpired access token is cut off immediately
        replay.cookies['access_token'] = access
        assert replay.get('/api/auth/sessions/').status_code == 401

    def test_logout_works_with_only_the_refresh_cookie(self, user):
        client, _, refresh = _client_for(user, access=False)
        client.post('/api/auth/logout/')
        assert DeviceSession.objects.get().revoked_at is not None


@pytest.mark.integration
class TestSessionsApi:
    def test_anonymous_gets_401(self, db):
        assert Client().get('/api/auth/sessions/').status_code == 401

    def test_list_shows_label_and_marks_current(self, user):
        c1, _, _ = _client_for(user, 'device-1111111111111111', ua=UA_CHROME)
        _client_for(user, 'device-2222222222222222', ua=UA_PHONE)

        body = c1.get('/api/auth/sessions/').json()

        assert len(body['sessions']) == 2
        current = [s for s in body['sessions'] if s['is_current']]
        assert len(current) == 1
        labels = {s['device_label'] for s in body['sessions']}
        assert labels == {'Chrome on Windows', 'Chrome on Android'}
        assert 'refresh_hash' not in body['sessions'][0]

    def test_revoke_one_device(self, user):
        c1, _, _ = _client_for(user, 'device-1111111111111111')
        _, _, other_refresh = _client_for(user, 'device-2222222222222222')
        other = DeviceSession.objects.get(device_id='device-2222222222222222')

        resp = c1.delete(f'/api/auth/sessions/{other.pk}/')

        assert resp.status_code == 200
        assert (
            Client()
            .post('/api/auth/refresh/', HTTP_COOKIE=f'refresh_token={other_refresh}')
            .status_code
            == 401
        )

    def test_revoke_others_keeps_current(self, user):
        c1, _, refresh1 = _client_for(user, 'device-1111111111111111')
        for i in (2, 3):
            _client_for(user, f'device-{i}{i}{i}{i}{i}{i}{i}{i}{i}{i}{i}{i}{i}{i}{i}{i}')

        body = c1.post('/api/auth/sessions/revoke-others/').json()

        assert body['revoked'] == 2
        assert DeviceSession.objects.filter(user=user, revoked_at__isnull=True).count() == 1
        assert c1.post('/api/auth/refresh/').status_code == 200

    def test_cannot_revoke_another_users_session(self, user, user2):
        c1, _, _ = _client_for(user)
        _client_for(user2, 'device-3333333333333333')
        theirs = DeviceSession.objects.get(user=user2)

        resp = c1.delete(f'/api/auth/sessions/{theirs.pk}/')

        assert resp.status_code == 404
        theirs.refresh_from_db()
        assert theirs.revoked_at is None

    def test_cannot_list_another_users_sessions(self, user, user2):
        c1, _, _ = _client_for(user)
        _client_for(user2, 'device-3333333333333333')
        ids = {s['id'] for s in c1.get('/api/auth/sessions/').json()['sessions']}
        assert ids == {str(s.pk) for s in DeviceSession.objects.filter(user=user)}

    def test_foreign_origin_cannot_revoke(self, user):
        c1, _, _ = _client_for(user)
        resp = c1.post('/api/auth/sessions/revoke-others/', HTTP_ORIGIN='https://evil.example')
        assert resp.status_code == 403

    def test_trusted_origin_can_revoke(self, user, settings):
        settings.CORS_ALLOWED_ORIGINS = ['https://app.example']
        c1, _, _ = _client_for(user)
        resp = c1.post('/api/auth/sessions/revoke-others/', HTTP_ORIGIN='https://app.example')
        assert resp.status_code == 200


@pytest.mark.integration
class TestRevokedAccessTokenIsRejectedImmediately:
    def test_revoked_session_access_token_stops_working(self, user):
        client, _, _ = _client_for(user)
        assert client.get('/api/auth/me/').status_code == 200
        from apps.accounts.services.token_service import revoke_session

        revoke_session(DeviceSession.objects.get())
        assert client.get('/api/auth/me/').status_code == 401

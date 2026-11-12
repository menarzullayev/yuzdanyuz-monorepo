"""
Device-session middleware.

DeviceCheckMiddleware (after JWTAuthMiddleware): when the access token's session
(``sid`` claim) has been revoked (logout elsewhere, "revoke device", device
limit, password change), the request is treated as anonymous right away.

DeviceLogoutMiddleware (response phase): clears the auth cookies after such a
revocation and sets the ``device_id`` cookie for flows that issued a session
without building their own response (allauth's OAuth callback).
"""

import logging

from django.conf import settings
from django.contrib.auth.models import AnonymousUser

from apps.accounts.services.token_service import (
    clear_auth_cookies,
    decode_access_token,
    is_session_revoked,
    set_device_cookie,
)

log = logging.getLogger(__name__)


class DeviceCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._check_session(request)
        return self.get_response(request)

    def _check_session(self, request) -> None:
        token = request.COOKIES.get('access_token')
        if not token:
            return
        payload = decode_access_token(token)
        if payload is None:
            # Expired/invalid: the refresh endpoint deals with it.
            return
        sid = payload.get('sid')
        if sid:
            request.session_sid = sid
            if is_session_revoked(sid):
                log.info('Revoked session used: user=%s sid=%s', payload.get('sub'), sid)
                request._force_logout = True
                request.user = AnonymousUser()


class DeviceLogoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(request, '_force_logout', False):
            clear_auth_cookies(response)
            request.user = AnonymousUser()
        new_device = getattr(request, '_device_id_to_set', None)
        if new_device and 'device_id' not in response.cookies:
            set_device_cookie(response, new_device, request.is_secure() or not settings.DEBUG)
        return response

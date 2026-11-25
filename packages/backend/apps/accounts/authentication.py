"""DRF authentication for the JWT-cookie flow.

JWTAuthMiddleware has already resolved ``request.user`` from the HttpOnly
access cookie, so this class only adopts it. It answers 401 (not 403) for
anonymous requests so the client knows to try /api/auth/refresh/. State-changing
requests carrying a foreign ``Origin`` are rejected: with SameSite=Lax cookies
that is the CSRF defence (no CSRF-token round trip, like the rest of the auth API).
"""

from urllib.parse import urlsplit

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import PermissionDenied

SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')


def _allowed_origins(request) -> set[str]:
    allowed = set(getattr(settings, 'CORS_ALLOWED_ORIGINS', []) or [])
    allowed |= set(getattr(settings, 'CSRF_TRUSTED_ORIGINS', []) or [])
    allowed.add(f'{request.scheme}://{request.get_host()}')
    return {o.rstrip('/') for o in allowed}


def origin_allowed(request) -> bool:
    """False only for a state-changing request carrying a foreign ``Origin`` header.

    Requests without ``Origin`` (non-browser clients, same-origin GET navigations) pass;
    browsers always send it on cross-site POSTs.
    """
    if request.method in SAFE_METHODS:
        return True
    origin = request.META.get('HTTP_ORIGIN')
    if not origin:
        return True
    parts = urlsplit(origin)
    return f'{parts.scheme}://{parts.netloc}' in _allowed_origins(request)


class CookieJWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        user = getattr(request._request, 'user', None)
        if user is None or not user.is_authenticated:
            return None
        if not origin_allowed(request):
            raise PermissionDenied('Origin not allowed.', code='origin_not_allowed')
        return (user, None)

    def authenticate_header(self, request):
        return 'Cookie'


class JWTCookieAuthentication(BaseAuthentication):
    """Default DRF authentication for requests signed in by the JWT cookie.

    ``JWTAuthMiddleware`` marks them; they carry no Django CSRF token (the Next.js app
    never fetches one), so DRF's SessionAuthentication used to reject every POST with
    "CSRF cookie not set". They get the same protection as the /api/auth/ views: the
    SameSite cookie plus a same-origin ``Origin`` check. Session-authenticated requests
    (admin, server-rendered pages) are left to SessionAuthentication and its CSRF check.
    """

    def authenticate(self, request):
        if not getattr(request._request, 'jwt_cookie_auth', False):
            return None
        user = request._request.user
        if not origin_allowed(request):
            raise PermissionDenied('Origin not allowed.', code='origin_not_allowed')
        return (user, None)

    def authenticate_header(self, request):
        return 'Cookie'

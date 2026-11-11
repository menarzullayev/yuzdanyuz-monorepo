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


class CookieJWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        user = getattr(request._request, 'user', None)
        if user is None or not user.is_authenticated:
            return None
        if request.method not in SAFE_METHODS:
            origin = request.META.get('HTTP_ORIGIN')
            if origin:
                parts = urlsplit(origin)
                normalized = f'{parts.scheme}://{parts.netloc}'
                if normalized not in _allowed_origins(request):
                    raise PermissionDenied('Origin not allowed.', code='origin_not_allowed')
        return (user, None)

    def authenticate_header(self, request):
        return 'Cookie'

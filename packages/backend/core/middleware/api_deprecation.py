"""
ISSUE-203 — API deprecation middleware.

Eski (unversioned) `/api/...` path'lariga Sunset + Deprecation header
qo'shadi. Frontend va monitoring shu signal'ni ko'rib /api/v1/ migration
priority'sini biladi.

RFC 8594 (Sunset) + IETF Deprecation Header Field draft.
"""

from datetime import UTC, datetime

# 6 oy ichida olib tashlanadi
SUNSET_DATE = datetime(2026, 11, 16, tzinfo=UTC)


class APIDeprecationMiddleware:
    """Eski /api/... endpoint'lariga Sunset + Deprecation header qo'shadi.

    /api/v1/, /api/auth/, /api/schema/ — skip (yangi yoki schema-only).
    """

    SKIP_PREFIXES = ('/api/v1/', '/api/auth/', '/api/schema/', '/api/forms/')

    def __init__(self, get_response):
        self.get_response = get_response
        self._sunset_str = SUNSET_DATE.strftime('%a, %d %b %Y %H:%M:%S GMT')

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path_info
        if path.startswith('/api/') and not any(path.startswith(p) for p in self.SKIP_PREFIXES):
            response['Sunset'] = self._sunset_str
            response['Deprecation'] = 'true'
            response['Link'] = f'</api/v1{path[len("/api") :]}>; rel="successor-version"'
        return response

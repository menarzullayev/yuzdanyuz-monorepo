"""
Test-only URL config for middleware chain integration tests.

Provides /test-echo/ that returns:
  - request.org.id (set by TenantMiddleware)
  - PostgreSQL session var app.current_org_id (set by RLSMiddleware)
  - is_authenticated, is_superuser (for assertions)

Used via @override_settings(ROOT_URLCONF='tests.integration._test_middleware_urls').
"""

from django.db import connection
from django.http import JsonResponse
from django.urls import path


def echo_view(request):
    org = getattr(request, 'org', None)
    org_id = str(org.id) if org else None

    with connection.cursor() as cur:
        cur.execute("SELECT current_setting('app.current_org_id', true)")
        rls_var = cur.fetchone()[0]

    return JsonResponse(
        {
            'request_org_id': org_id,
            'rls_app_current_org_id': rls_var or None,
            'is_authenticated': request.user.is_authenticated,
            'is_superuser': bool(request.user.is_authenticated and request.user.is_superuser),
            'is_staff': bool(request.user.is_authenticated and request.user.is_staff),
        }
    )


urlpatterns = [
    path('test-echo/', echo_view, name='test-echo'),
]

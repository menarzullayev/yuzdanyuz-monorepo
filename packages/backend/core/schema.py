"""OpenAPI schema glue for drf-spectacular (audit D04).

* The two cookie authentications get OpenAPI descriptions, so every view that uses
  them stops producing "could not resolve authenticator" warnings.
* Plain APIViews answer GET on both ``/things/`` and ``/things/{id}/``; drf-spectacular
  calls both ``*_retrieve`` and has to append numbers. A GET on a path that does not
  end in a parameter is named ``*_list`` instead.
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.openapi import AutoSchema as _AutoSchema


class _AccessCookieScheme(OpenApiAuthenticationExtension):
    name = 'accessCookie'
    priority = 1

    def get_security_definition(self, auto_schema):
        return {
            'type': 'apiKey',
            'in': 'cookie',
            'name': 'access_token',
            'description': (
                'HttpOnly JWT access cookie set by /api/auth/login/. State-changing '
                'requests must come from an allowed Origin; no CSRF token is needed.'
            ),
        }


class JWTCookieScheme(_AccessCookieScheme):
    target_class = 'apps.accounts.authentication.JWTCookieAuthentication'


class CookieJWTScheme(_AccessCookieScheme):
    # Same cookie; a separate name because spectacular keys components by name.
    name = 'accessCookieAuthApi'
    target_class = 'apps.accounts.authentication.CookieJWTAuthentication'


class AutoSchema(_AutoSchema):
    def get_operation_id(self):
        operation_id = super().get_operation_id()
        last = self.path.rstrip('/').rsplit('/', 1)[-1]
        if self.method == 'GET' and operation_id.endswith('_retrieve') and not last.startswith('{'):
            return operation_id[: -len('_retrieve')] + '_list'
        return operation_id

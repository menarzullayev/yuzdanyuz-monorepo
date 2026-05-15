"""
AccountAdapter.login() tomonidan request._jwt_* ga yozilgan tokenlarni
response cookie ga ko'chiradi.
"""

from apps.accounts.services.token_service import set_auth_cookies


class JWTCookieWriterMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        access = getattr(request, '_jwt_access', None)
        refresh = getattr(request, '_jwt_refresh', None)
        if access and refresh:
            is_secure = (
                request.is_secure()
                or not __import__('django.conf', fromlist=['settings']).settings.DEBUG
            )
            set_auth_cookies(response, access, refresh, is_secure=is_secure)
        return response

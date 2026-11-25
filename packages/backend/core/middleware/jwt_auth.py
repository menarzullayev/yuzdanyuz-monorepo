"""
JWT cookie → request.user middleware.

AuthenticationMiddleware dan KEYIN, TenantMiddleware dan OLDIN joylashtiriladi.
Cookie da valid access_token bo'lsa — foydalanuvchi authenticate qilinadi.
"""

from django.contrib.auth import authenticate


class JWTAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = request.COOKIES.get('access_token')
        if token and not request.user.is_authenticated:
            user = authenticate(request, token=token)
            if user is not None:
                request.user = user
                # DRF's JWTCookieAuthentication protects these requests with the Origin
                # check instead of Django's CSRF token, which the SPA never sends.
                request.jwt_cookie_auth = True
        return self.get_response(request)

"""
Rate Limit Middleware — har request'da limit'ni tekshiradi.

Tier aniqlash:
  /api/auth/login/   → TIER_LOGIN (5/min)
  /api/...           → TIER_API   (30/min)
  /static/, /media/  → TIER_STATIC (100/min)
  Boshqa             → TIER_API

Identifier:
  - Authenticated: f"user:{user.pk}"
  - Anonymous:     f"ip:{client_ip}"

Admin/superuser bypass: hech qanday limit qo'llanmaydi.
Block bo'lsa: HTTP 429 + Retry-After header.
"""

import logging

from django.http import JsonResponse

from core.utils import rate_limiter as rl

log = logging.getLogger(__name__)


# Login URL pattern'lari (substring match, tezkor)
LOGIN_PATH_MARKERS = (
    '/auth/login',
    '/api/auth/login',
    '/api/v1/auth/login',
    '/accounts/login',
    '/auth/otp',
    '/api/auth/otp',
)

STATIC_PREFIXES = ('/static/', '/media/', '/favicon.ico')


def _client_ip(request) -> str:
    """X-Forwarded-For (first IP) yoki REMOTE_ADDR."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


def _identifier(request) -> str:
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return f'user:{user.pk}'
    return f'ip:{_client_ip(request)}'


def _select_tier(path: str) -> rl.RateTier:
    if any(marker in path for marker in LOGIN_PATH_MARKERS):
        return rl.TIER_LOGIN
    if path.startswith(STATIC_PREFIXES):
        return rl.TIER_STATIC
    return rl.TIER_API


def _is_bypass(request) -> bool:
    user = getattr(request, 'user', None)
    return bool(user and user.is_authenticated and (user.is_superuser or user.is_staff))


class RateLimitMiddleware:
    """
    Process_request darajasida limit tekshiradi.
    request.user shakllangach (JWTAuthMiddleware'dan keyin) joylashishi kerak.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if _is_bypass(request):
            return self.get_response(request)

        identifier = _identifier(request)
        tier = _select_tier(request.path)

        try:
            rl.check(identifier, tier)
        except rl.RateLimitExceeded as e:
            return self._too_many_requests(request, e)
        except Exception as e:
            # Redis ishlamayotgan bo'lsa, request'ni o'tkazib yuboramiz
            log.error('Rate limit middleware error: %s', e)

        return self.get_response(request)

    @staticmethod
    def _too_many_requests(request, exc: rl.RateLimitExceeded):
        body = {
            'error': 'rate_limit_exceeded',
            'message': "Juda ko'p so'rov. Iltimos, biroz kuting.",
            'tier': exc.tier,
            'retry_after': exc.retry_after,
        }
        resp = JsonResponse(body, status=429)
        resp['Retry-After'] = str(exc.retry_after)
        return resp

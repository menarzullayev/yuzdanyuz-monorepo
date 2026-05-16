"""Rate Limit Middleware — endpoint-aware tier selection.

═══════════════════════════════════════════════════════════════════════
TIER SELECTION (path + method bo'yicha)
═══════════════════════════════════════════════════════════════════════

LOGIN paths (5/min — brute force protection):
  /accounts/login/, /accounts/password/, /accounts/signup/
  /api/auth/login/, /api/auth/otp/, /api/auth/password-reset/
  /api/v1/auth/* (login, otp, password)

ANTI-CHEAT (30/min — spam guard):
  /api/v1/exams/*/anticheat/  (POST)

STATIC (200/min — Apache usually serves these):
  /static/, /media/, /favicon.ico

BROWSE (120/min — read-heavy, NAT-friendly):
  GET on /api/v1/* and /api/*

API mutation (60/min — default):
  POST/PUT/PATCH/DELETE on /api/*
  /admin/* (all methods)
  All other paths

═══════════════════════════════════════════════════════════════════════
IDENTIFIER (NAT-aware)
═══════════════════════════════════════════════════════════════════════

  Authenticated: f"user:{user.pk}"  (NAT immune)
  Anonymous:     f"ip:{client_ip}"

═══════════════════════════════════════════════════════════════════════
BYPASS
═══════════════════════════════════════════════════════════════════════

  is_superuser or is_staff → no rate limit applied.

═══════════════════════════════════════════════════════════════════════
ERRORS
═══════════════════════════════════════════════════════════════════════

  Limit exceeded → HTTP 429 + Retry-After header + JSON body with tier name.
  Redis errors → request passes through (fail-open for availability).
"""

import logging

from django.http import JsonResponse

from core.utils import rate_limiter as rl

log = logging.getLogger(__name__)


# ── Path classifiers ────────────────────────────────────────────────────────

# Login + auth flows — strict 5/min (substring match — works under subpath too)
LOGIN_PATH_MARKERS = (
    '/accounts/login',
    '/accounts/password',
    '/accounts/signup',
    '/auth/login',
    '/auth/otp',
    '/auth/password-reset',
    '/api/auth/login',
    '/api/auth/otp',
    '/api/auth/password',
    '/api/v1/auth/login',
    '/api/v1/auth/otp',
    '/api/v1/auth/password',
)

# Anti-cheat endpoint — substring match
ANTICHEAT_PATH_MARKERS = ('/anticheat/',)

STATIC_PREFIXES = ('/static/', '/media/', '/favicon.ico')

# Browse vs mutation split — applies under any /api/ prefix
API_PATH_MARKERS = ('/api/',)
READ_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})


# ── Helpers ─────────────────────────────────────────────────────────────────


def _client_ip(request) -> str:
    """X-Forwarded-For (first IP) yoki REMOTE_ADDR."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


def _identifier(request) -> str:
    """User-level bucket if authenticated (NAT immune), else IP-level."""
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return f'user:{user.pk}'
    return f'ip:{_client_ip(request)}'


def _select_tier(request) -> rl.RateTier:
    """Path + method bo'yicha eng moslashgan tier'ni qaytaradi.

    Priority: login > anticheat > static > api(browse vs mutation) > default.
    """
    path = request.path
    method = request.method

    if any(marker in path for marker in LOGIN_PATH_MARKERS):
        return rl.TIER_LOGIN

    if any(marker in path for marker in ANTICHEAT_PATH_MARKERS):
        return rl.TIER_ANTICHEAT

    if path.startswith(STATIC_PREFIXES):
        return rl.TIER_STATIC

    if any(marker in path for marker in API_PATH_MARKERS):
        return rl.TIER_BROWSE if method in READ_METHODS else rl.TIER_API

    # Boshqa hammasi (admin, html pages) — default API tier
    return rl.TIER_API


def _is_bypass(request) -> bool:
    user = getattr(request, 'user', None)
    return bool(user and user.is_authenticated and (user.is_superuser or user.is_staff))


# ── Middleware ──────────────────────────────────────────────────────────────


class RateLimitMiddleware:
    """Process_request darajasida limit tekshiradi.

    request.user shakllangach (JWTAuthMiddleware'dan keyin) joylashishi kerak.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if _is_bypass(request):
            return self.get_response(request)

        identifier = _identifier(request)
        tier = _select_tier(request)

        try:
            rl.check(identifier, tier)
        except rl.RateLimitExceeded as e:
            return self._too_many_requests(e)
        except Exception as e:
            # Redis ishlamayotgan bo'lsa, request'ni o'tkazib yuboramiz (fail-open)
            log.error('Rate limit middleware error: %s', e)

        return self.get_response(request)

    @staticmethod
    def _too_many_requests(exc: rl.RateLimitExceeded):
        body = {
            'error': 'rate_limit_exceeded',
            'message': "Juda ko'p so'rov. Iltimos, biroz kuting.",
            'tier': exc.tier,
            'retry_after': exc.retry_after,
        }
        resp = JsonResponse(body, status=429)
        resp['Retry-After'] = str(exc.retry_after)
        return resp

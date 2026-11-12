"""
Liveness/readiness probes for K8s.

  /health/        — liveness (always 200 if process is running)
  /health/ready/  — readiness (200 only if DB + Redis + external deps OK)

ISSUE-105: tashqi servis check'lar qo'shildi (Anthropic, Telegram, Payme).
Har biri optional — config'da yoqilgan bo'lsa tekshiriladi. Stub mode'dagi
servislar skip qilinadi.

Quick mode is cheap, dependency-free and public (kubelet probes, uptime checks).
Deep mode (`?deep=1`) calls third-party APIs, so it needs `X-Health-Token:
<HEALTH_DEEP_TOKEN>` or a staff session; anyone else gets 403. Failures are
logged by exception type only: an httpx error message contains the request URL,
and the Telegram URL contains the bot token.
"""

import hmac
import logging

from django.conf import settings
from django.db import connection
from django.http import JsonResponse

logger = logging.getLogger(__name__)

# Tashqi check timeout (har biri uchun) — ready endpoint < 5s bo'lishi shart
_EXTERNAL_TIMEOUT = 2.0


def liveness(request):
    return JsonResponse({'status': 'ok'}, status=200)


def _check_database() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        return True
    except Exception as exc:
        logger.warning('readiness: db check failed: %s', exc)
        return False


def _check_redis() -> bool:
    try:
        import redis

        r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
        r.ping()
        return True
    except Exception as exc:
        logger.warning('readiness: redis check failed: %s', exc)
        return False


def _check_anthropic() -> bool | None:
    """Anthropic API key set bo'lsa — endpoint reachable ekanini tekshirish.

    Real `messages.create` qilmaslik — pul ketadi. Faqat HTTP HEAD'da TLS
    handshake muvaffaqiyatli bo'lsa, "reachable" deb sanaymiz.
    """
    if not getattr(settings, 'ANTHROPIC_API_KEY', ''):
        return None  # Configured emas — skip
    try:
        import httpx

        with httpx.Client(timeout=_EXTERNAL_TIMEOUT) as client:
            # Anthropic API'da public root'i 404 qaytaradi (auth siz) lekin reachable
            resp = client.head('https://api.anthropic.com/')
            return resp.status_code < 500
    except Exception as exc:
        logger.warning('readiness: anthropic check failed: %s', type(exc).__name__)
        return False


def _check_telegram() -> bool | None:
    """Telegram bot getMe — token mavjudligi va API reachable ekanini tasdiqlash."""
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        return None
    try:
        import httpx

        with httpx.Client(timeout=_EXTERNAL_TIMEOUT) as client:
            resp = client.get(f'https://api.telegram.org/bot{token}/getMe')
            return resp.status_code == 200 and resp.json().get('ok') is True
    except Exception as exc:
        logger.warning('readiness: telegram check failed: %s', type(exc).__name__)
        return False


def _check_payments() -> bool | None:
    """Payme/Click stub mode'da — skip. Real mode'da provider reachable test."""
    if not getattr(settings, 'ENABLE_REAL_PAYMENTS', False):
        return None  # Stub mode — har doim "OK"
    try:
        import httpx

        with httpx.Client(timeout=_EXTERNAL_TIMEOUT) as client:
            # Payme va Click sandbox endpoint'lari (production'da real URL'lar)
            for url in ('https://checkout.paycom.uz', 'https://api.click.uz'):
                resp = client.head(url)
                if resp.status_code >= 500:
                    return False
        return True
    except Exception as exc:
        logger.warning('readiness: payments check failed: %s', type(exc).__name__)
        return False


def _deep_allowed(request) -> bool:
    expected = getattr(settings, 'HEALTH_DEEP_TOKEN', '')
    given = request.headers.get('X-Health-Token', '')
    if expected and given and hmac.compare_digest(given.encode(), expected.encode()):
        return True
    user = getattr(request, 'user', None)
    return bool(user is not None and user.is_authenticated and user.is_staff)


def readiness(request):
    """
    Quick-mode (default): faqat DB + Redis.
    ?deep=1: tashqi servislar ham (Anthropic, Telegram, Payme).

    K8s readiness probe quick-mode'da har 10s ishlaydi. Deep-mode'ni faqat
    Prometheus blackbox exporter har 1 daqiqada ishlatishi mumkin.
    """
    deep = request.GET.get('deep') == '1'
    if deep and not _deep_allowed(request):
        return JsonResponse({'status': 'forbidden', 'mode': 'deep'}, status=403)

    checks: dict[str, bool | None] = {
        'database': _check_database(),
        'redis': _check_redis(),
    }
    if deep:
        checks['anthropic'] = _check_anthropic()
        checks['telegram'] = _check_telegram()
        checks['payments'] = _check_payments()

    # None = skip (configured emas), False = unhealthy
    required_ok = all(v is True for v in checks.values() if v is not None)

    return JsonResponse(
        {
            'status': 'ready' if required_ok else 'degraded',
            'mode': 'deep' if deep else 'quick',
            'checks': checks,
        },
        status=200 if required_ok else 503,
    )

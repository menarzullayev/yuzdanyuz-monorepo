"""Per-account login throttle with time-based exponential backoff.

The IP tier in ``core.utils.rate_limiter`` stops one source from guessing
fast; this module stops many sources from guessing one account's password.

After ``LOGIN_FAILURE_FREE_ATTEMPTS`` consecutive failures the account is
locked for ``LOGIN_BACKOFF_BASE_SECONDS``, doubling with every further
failure up to ``LOGIN_BACKOFF_MAX_SECONDS``. Attempts made while locked are
refused without being counted, so hammering a locked account does not make
the lock longer: a victim is never locked out for more than the cap, and
other sign-in methods (OTP, Telegram) are not affected at all. A successful
login clears the counter.

Redis keys (the account key is a hash, so no login names land in Redis):

  auth:fail:{key}   consecutive failure count (TTL LOGIN_FAILURE_WINDOW_SECONDS)
  auth:lock:{key}   unix time the lock ends    (TTL = lock length)
"""

from __future__ import annotations

import hashlib
import logging
import math
import time

from django.conf import settings

from core.utils import rate_limiter as rl

log = logging.getLogger(__name__)


class LoginThrottled(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f'Login throttled. Retry after {retry_after}s')


def _now() -> float:
    return time.time()


def _setting(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def account_key(identifier: str | int) -> str:
    """Stable, non-reversible bucket key for an account identifier."""
    norm = str(identifier).strip().lower()
    return hashlib.sha256(norm.encode()).hexdigest()[:32]


def _lock_seconds(failures: int) -> int:
    free = _setting('LOGIN_FAILURE_FREE_ATTEMPTS', 5)
    if failures < free:
        return 0
    base = _setting('LOGIN_BACKOFF_BASE_SECONDS', 30)
    cap = _setting('LOGIN_BACKOFF_MAX_SECONDS', 900)
    # Exponent is clamped so a huge counter cannot overflow the float.
    return min(cap, base * 2 ** min(failures - free, 32))


def check(key: str) -> None:
    """Raise ``LoginThrottled`` while the account is in backoff."""
    try:
        until = rl._redis().get(f'auth:lock:{key}')
    except Exception as e:
        log.error('Login throttle unavailable (check): %s', e)
        return
    if until is None:
        return
    remaining = float(until) - _now()
    if remaining > 0:
        raise LoginThrottled(retry_after=max(1, math.ceil(remaining)))


def register_failure(key: str) -> int:
    """Record a failed attempt; return the lock length it started (0 if none)."""
    try:
        r = rl._redis()
        window = _setting('LOGIN_FAILURE_WINDOW_SECONDS', 3600)
        pipe = r.pipeline()
        pipe.incr(f'auth:fail:{key}')
        pipe.expire(f'auth:fail:{key}', window)
        failures, _ = pipe.execute()
        seconds = _lock_seconds(int(failures))
        if seconds:
            r.set(f'auth:lock:{key}', str(_now() + seconds), ex=seconds + 5)
            log.warning('Login backoff: account=%s failures=%s lock=%ss', key, failures, seconds)
        return seconds
    except Exception as e:
        log.error('Login throttle unavailable (failure): %s', e)
        return 0


def reset(key: str) -> None:
    try:
        rl._redis().delete(f'auth:fail:{key}', f'auth:lock:{key}')
    except Exception as e:
        log.error('Login throttle unavailable (reset): %s', e)

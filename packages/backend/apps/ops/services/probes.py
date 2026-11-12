"""Health probe: DB + Redis (+ optional AI provider ping), with latency and state-change alerts."""

import logging
import time

from django.conf import settings

from apps.ops.models import ProbeResult
from apps.ops.services import alerts

log = logging.getLogger(__name__)

STATE_KEY = 'probe:state'


def _timed(fn):
    start = time.monotonic()
    try:
        result = fn()
    except Exception as e:
        log.warning('probe check failed: %s', e)
        result = False
    return result, int((time.monotonic() - start) * 1000)


def run_probe() -> ProbeResult:
    from core import health

    db_ok, db_ms = _timed(health._check_database)
    redis_ok, redis_ms = _timed(health._check_redis)
    ai_ok = ai_ms = None
    if getattr(settings, 'OPS_PROBE_AI', False):
        ai_ok, ai_ms = _timed(health._check_anthropic)  # None result = not configured
        if ai_ok is None:
            ai_ms = None

    probe = ProbeResult.objects.create(
        ok=bool(db_ok and redis_ok),
        db_ok=bool(db_ok),
        redis_ok=bool(redis_ok),
        ai_ok=ai_ok,
        db_ms=db_ms,
        redis_ms=redis_ms,
        ai_ms=ai_ms,
    )
    _alert_on_change(probe)
    return probe


def _alert_on_change(probe: ProbeResult) -> None:
    new = 'up' if probe.ok else 'down'
    previous = alerts.change_state(STATE_KEY, new)
    if previous is None:
        return  # no change (or a concurrent probe already reported it)
    if previous == '' and probe.ok:
        return  # very first probe and healthy: nothing to report
    if probe.ok:
        alerts.alert_admin('<b>Tiklandi</b>: baza va Redis yana javob bermoqda.')
    else:
        failed = [n for n, ok in (('baza', probe.db_ok), ('Redis', probe.redis_ok)) if not ok]
        alerts.alert_admin(f'<b>Muammo</b>: javob bermayapti: {alerts.esc(", ".join(failed))}.')

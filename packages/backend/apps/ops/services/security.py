"""Security event recording and the "one noisy source" alert.

``record`` is called from existing code paths (login throttle, rate limiter, payment
webhook, recovery). It must never change their behaviour: every failure is swallowed.
"""

import ipaddress
import logging
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.ops.models import SecurityEvent
from apps.ops.services import alerts

log = logging.getLogger(__name__)

ALERT_THRESHOLD = 12  # events from one source ...
ALERT_WINDOW_SECONDS = 600  # ... within 10 minutes
ALERT_COOLDOWN_SECONDS = 3600  # at most one alert per source per hour


def _valid_ip(value) -> str | None:
    try:
        return str(ipaddress.ip_address(value)) if value else None
    except ValueError:
        return None


def _source(actor, ip) -> str:
    if actor is not None and getattr(actor, 'is_authenticated', False):
        return f'user:{actor.pk}'
    return f'ip:{ip}' if ip else 'ip:unknown'


def record(
    event_type: str,
    *,
    request=None,
    user=None,
    ip: str | None = None,
    path: str = '',
    reason: str = '',
    sample_seconds: int = 0,
) -> SecurityEvent | None:
    """Store one event; returns it (None when skipped by sampling or on failure).

    ``user`` is the account the event is about (e.g. the login target). The source is the
    authenticated actor of ``request`` if any, else its IP. ``sample_seconds`` keeps flood
    events (429s) to one row per source+path per window.
    """
    try:
        actor = None
        ip = _valid_ip(ip)
        if request is not None:
            from core.utils.client_ip import get_client_ip

            ip = ip or _valid_ip(get_client_ip(request))
            path = path or request.path
            actor = getattr(request, 'user', None)
        source = _source(actor, ip)
        if sample_seconds and not cache.add(
            f'ops:sec:{event_type}:{source}:{path[:120]}', 1, sample_seconds
        ):
            return None
        target = user if user is not None and getattr(user, 'pk', None) else None
        # Savepoint: a failure here must not poison the caller's transaction.
        with transaction.atomic():
            event = SecurityEvent.objects.create(
                event_type=event_type,
                source=source[:80],
                ip=ip or None,
                user=target,
                path=(path or '')[:300],
                reason=(reason or '')[:200],
            )
        _maybe_alert(event)
        return event
    except Exception as e:
        log.error('Security event not recorded: %s', e)
        return None


def _count(source: str, since) -> int:
    qs = SecurityEvent.objects.filter(created_at__gte=since)
    if source.startswith('user:'):
        qs = qs.filter(Q(source=source) | Q(user_id=source.split(':', 1)[1]))
    else:
        qs = qs.filter(source=source)
    return qs.count()


def _maybe_alert(event: SecurityEvent) -> None:
    keys = {event.source}
    if event.user_id:
        keys.add(f'user:{event.user_id}')
    since = timezone.now() - timedelta(seconds=ALERT_WINDOW_SECONDS)
    for source in sorted(keys):
        if _count(source, since) < ALERT_THRESHOLD:
            continue
        if not alerts.claim_cooldown(f'sec:{source}', ALERT_COOLDOWN_SECONDS):
            continue
        alerts.alert_admin(
            '<b>Xavfsizlik ogohlantirishi</b>\n'
            f'Manba: <code>{alerts.esc(source)}</code>\n'
            f'{ALERT_WINDOW_SECONDS // 60} daqiqada {ALERT_THRESHOLD}+ hodisa '
            f'(oxirgisi: {alerts.esc(event.event_type)}).'
        )

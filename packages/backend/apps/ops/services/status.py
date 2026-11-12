"""Public status page payload: overall state, incidents, maintenance, 90-day uptime."""

import ipaddress
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.ops.models import ProbeResult, StatusIncident

TASHKENT = ZoneInfo('Asia/Tashkent')
PROBE_INTERVAL = 300
PROBES_PER_DAY = 86400 // PROBE_INTERVAL  # 288
UPTIME_DAYS = 90
RECENT_RESOLVED_DAYS = 14

_IPV4 = re.compile(r'(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])')
_IPV6_CANDIDATE = re.compile(r'[0-9A-Fa-f:]*:[0-9A-Fa-f:.]*')


def contains_ip_address(text: str) -> bool:
    """True if ``text`` holds an IPv4 or IPv6 address (internal details must not leak)."""
    for m in _IPV4.finditer(text):
        try:
            ipaddress.ip_address(m.group())
            return True
        except ValueError:
            continue
    for m in _IPV6_CANDIDATE.finditer(text):
        token = m.group().strip('.')
        try:
            ipaddress.IPv6Address(token)
            return True
        except ValueError:
            continue
    return False


def local_iso(dt) -> str | None:
    return dt.astimezone(TASHKENT).isoformat() if dt else None


def _incident_payload(inc: StatusIncident) -> dict:
    return {
        'id': str(inc.id),
        'kind': inc.kind,
        'title': inc.title,
        'impact': inc.impact,
        'status': inc.status,
        'started_at': local_iso(inc.started_at),
        'ended_at': local_iso(inc.resolved_at or inc.ends_at),
        'resolved': inc.resolved_at is not None,
        'updates': [
            {'status': u.status, 'message': u.message, 'at': local_iso(u.created_at)}
            for u in inc.updates.all()
        ],
    }


def uptime_days(now=None) -> dict:
    """Per-day uptime for the last 90 local days.

    uptime = ok probes / expected probes (288 per full day); missing probes count as downtime.
    Days before the first probe ever recorded have no data (null). The first and the current
    day are only expected from the first probe / until ``now``.
    """
    now = now or timezone.now()
    today = now.astimezone(TASHKENT).date()
    first_day = today - timedelta(days=UPTIME_DAYS - 1)
    start = datetime.combine(first_day, time.min, tzinfo=TASHKENT)
    first_probe = (
        ProbeResult.objects.order_by('checked_at').values_list('checked_at', flat=True).first()
    )
    rows = (
        ProbeResult.objects.filter(checked_at__gte=start)
        .annotate(day=TruncDate('checked_at', tzinfo=TASHKENT))
        .values('day')
        .annotate(total=Count('id'), good=Count('id', filter=Q(ok=True)))
    )
    by_day = {r['day']: r for r in rows}

    days, sum_good, sum_expected = [], 0, 0
    for i in range(UPTIME_DAYS):
        day = first_day + timedelta(days=i)
        day_start = datetime.combine(day, time.min, tzinfo=TASHKENT)
        window_start = max(day_start, first_probe) if first_probe else None
        window_end = min(day_start + timedelta(days=1), now)
        if window_start is None or window_end <= window_start:
            days.append({'date': day.isoformat(), 'uptime_pct': None})
            continue
        expected = min(
            PROBES_PER_DAY, int((window_end - window_start).total_seconds() // PROBE_INTERVAL) + 1
        )
        good = by_day.get(day, {}).get('good', 0)
        good = min(good, expected)
        sum_good += good
        sum_expected += expected
        days.append({'date': day.isoformat(), 'uptime_pct': round(100 * good / expected, 2)})
    overall = round(100 * sum_good / sum_expected, 2) if sum_expected else None
    return {'days': days, 'overall_pct': overall}


def build_status(now=None) -> dict:
    now = now or timezone.now()
    open_qs = StatusIncident.objects.filter(resolved_at__isnull=True).prefetch_related('updates')
    active_incidents = [i for i in open_qs if i.kind == 'incident' and i.started_at <= now]
    maintenance = [i for i in open_qs if i.kind == 'maintenance']
    in_window = [m for m in maintenance if m.started_at <= now]
    recent = StatusIncident.objects.filter(
        resolved_at__isnull=False, resolved_at__gte=now - timedelta(days=RECENT_RESOLVED_DAYS)
    ).prefetch_related('updates')

    last = ProbeResult.objects.first()
    impacts = {i.impact for i in active_incidents}
    if 'major' in impacts:
        overall = 'major_outage'
    elif 'partial' in impacts:
        overall = 'partial_outage'
    elif last is not None and not last.ok and now - last.checked_at <= timedelta(minutes=15):
        overall = 'degraded'
    elif in_window:
        overall = 'maintenance'
    else:
        overall = 'operational'

    components = None
    if last is not None:
        components = {
            'platform': 'operational' if last.ok else 'down',
            **(
                {'ai': 'operational' if last.ai_ok else 'degraded'}
                if last.ai_ok is not None
                else {}
            ),
        }
    return {
        'status': overall,
        'updated_at': local_iso(now),
        'timezone': 'Asia/Tashkent',
        'components': components,
        'incidents': [_incident_payload(i) for i in active_incidents],
        'maintenance': [_incident_payload(m) for m in maintenance],
        'recent_incidents': [_incident_payload(i) for i in recent],
        'uptime': uptime_days(now),
    }

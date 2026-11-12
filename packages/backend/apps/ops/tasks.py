"""Ops background tasks (platform-level data only, no tenant context needed)."""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

PROBE_RETENTION_DAYS = 120
EVENT_RETENTION_DAYS = 90


@shared_task(name='ops.run_health_probe', ignore_result=True)
def run_health_probe() -> dict:
    from apps.ops.services.probes import run_probe

    probe = run_probe()
    return {'ok': probe.ok}


@shared_task(name='ops.send_admin_alert', ignore_result=True)
def send_admin_alert(text: str) -> int:
    from apps.ops.services.alerts import deliver

    return deliver(text)


@shared_task(name='ops.cleanup_old_records', ignore_result=True)
def cleanup_old_records() -> dict:
    from apps.ops.models import ProbeResult, SecurityEvent

    now = timezone.now()
    probes, _ = ProbeResult.objects.filter(
        checked_at__lt=now - timedelta(days=PROBE_RETENTION_DAYS)
    ).delete()
    events, _ = SecurityEvent.objects.filter(
        created_at__lt=now - timedelta(days=EVENT_RETENTION_DAYS)
    ).delete()
    logger.info('ops cleanup: %d probes, %d security events', probes, events)
    return {'probes': probes, 'events': events}

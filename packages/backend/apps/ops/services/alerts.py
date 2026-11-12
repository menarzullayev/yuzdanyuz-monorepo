"""Telegram admin alert hook and the de-duplication helpers behind it."""

import html
import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.ops.models import AlertState
from apps.ops.services import bot_config

log = logging.getLogger(__name__)


def alert_admin(text: str) -> None:
    """Queue an alert for the admin chats; never raises into the caller."""
    try:
        from apps.ops.tasks import send_admin_alert

        send_admin_alert.delay(text)
    except Exception as e:
        log.error('Could not queue admin alert: %s', e)


def deliver(text: str) -> int:
    """Send ``text`` to every configured admin chat; returns how many accepted it."""
    from apps.accounts.services.bot_sender import send_message

    sent = 0
    for chat_id in bot_config.get_admin_chat_ids():
        if send_message(chat_id, text):
            sent += 1
    return sent


def esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def claim_cooldown(key: str, seconds: int) -> bool:
    """True for exactly one caller per ``seconds`` window for ``key`` (atomic, DB-backed)."""
    now = timezone.now()
    cutoff = now - timedelta(seconds=seconds)
    # Existing row older than the window: only one concurrent UPDATE can win.
    if AlertState.objects.filter(key=key, updated_at__lte=cutoff).update(updated_at=now):
        return True
    try:
        with transaction.atomic():
            AlertState.objects.create(key=key, value='1', updated_at=now)
        return True
    except IntegrityError:
        return False  # row exists and is still inside the window


def change_state(key: str, new_value: str) -> str | None:
    """Set ``key`` to ``new_value``; return the previous value if it changed, else None.

    Returns '' when the key did not exist yet. Concurrent callers cannot both observe the change.
    """
    stale = AlertState.objects.exclude(value=new_value).filter(key=key)
    previous = stale.values_list('value', flat=True).first()
    if previous is not None:
        if stale.update(value=new_value, updated_at=timezone.now()):
            return previous
        return None
    try:
        with transaction.atomic():
            AlertState.objects.create(key=key, value=new_value)
        return ''
    except IntegrityError:
        return None

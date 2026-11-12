"""Notification hooks for the organizations app.

Telegram (or any other channel) delivery is implemented elsewhere: connect a receiver
to ``partnership_application_created`` -- nothing is sent from this app.

    from django.dispatch import receiver
    from apps.organizations.hooks import partnership_application_created

    @receiver(partnership_application_created)
    def notify(sender, application, **kwargs): ...
"""

import logging

from django.db import transaction
from django.dispatch import Signal

log = logging.getLogger(__name__)

# Sent after a NEW public partnership application is committed (never for honeypot
# hits or duplicates). kwargs: application (PartnershipApplication).
partnership_application_created = Signal()


def notify_partnership_application_created(application) -> None:
    """Fire the hook once the surrounding transaction commits; receiver errors are logged."""

    def _send():
        for receiver, result in partnership_application_created.send_robust(
            sender=type(application), application=application
        ):
            if isinstance(result, Exception):
                log.error('partnership hook %r failed: %s', receiver, result)

    transaction.on_commit(_send)

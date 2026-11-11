"""Sign-in methods of an account (password, Telegram, Google) and (un)linking them."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import CustomUser


class TelegramLinkError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def google_account(user: CustomUser):
    from allauth.socialaccount.models import SocialAccount

    return SocialAccount.objects.filter(user=user, provider='google').first()


def active_methods(user: CustomUser) -> set[str]:
    """The sign-in methods the user can currently use."""
    methods = set()
    if user.has_usable_password():
        methods.add('password')
    if user.telegram_id:
        methods.add('telegram')
    if google_account(user) is not None:
        methods.add('google')
    return methods


def can_unlink(user: CustomUser, method: str) -> bool:
    """Unlinking is allowed only if another method remains."""
    return method in active_methods(user) and len(active_methods(user) - {method}) >= 1


def link_telegram(user: CustomUser, telegram_id: int, profile: dict | None = None) -> None:
    """Attach an already-attested Telegram id (signed initData or bot confirmation)."""
    profile = profile or {}
    if user.telegram_id == telegram_id:
        return
    if user.telegram_id is not None:
        raise TelegramLinkError('already_linked', 'Another Telegram account is already linked.')
    if CustomUser.objects.filter(telegram_id=telegram_id).exclude(pk=user.pk).exists():
        raise TelegramLinkError('telegram_in_use', 'This Telegram account belongs to another user.')
    user.telegram_id = telegram_id
    user.telegram_username = profile.get('username') or None
    user.telegram_photo_url = profile.get('photo_url') or None
    user.telegram_language = profile.get('language_code') or None
    user.tg_linked_at = timezone.now()
    try:
        with transaction.atomic():
            user.save(
                update_fields=[
                    'telegram_id',
                    'telegram_username',
                    'telegram_photo_url',
                    'telegram_language',
                    'tg_linked_at',
                ]
            )
    except IntegrityError as e:
        user.telegram_id = None
        raise TelegramLinkError(
            'telegram_in_use', 'This Telegram account belongs to another user.'
        ) from e


def unlink_telegram(user: CustomUser) -> None:
    user.telegram_id = None
    user.telegram_username = None
    user.telegram_photo_url = None
    user.tg_linked_at = None
    user.save(
        update_fields=['telegram_id', 'telegram_username', 'telegram_photo_url', 'tg_linked_at']
    )

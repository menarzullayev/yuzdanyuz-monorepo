"""JSON shapes shared by several auth endpoints."""

from apps.accounts.models import CustomUser


def username_change_available(user: CustomUser) -> bool:
    """Auto-generated usernames (Telegram/Google) get exactly one free change."""
    return bool(user.username_is_auto and user.username_changed_at is None)


def user_payload(user: CustomUser) -> dict:
    """The user object returned by login/register/me (never contains secrets)."""
    org = user.primary_organization
    return {
        'id': str(user.pk),
        'username': user.username,
        'first_name': user.first_name or None,
        'last_name': user.last_name or None,
        'display_name': user.display_name,
        'email': user.email or None,
        'email_verified': False,
        'phone_number': user.phone_number or None,
        'avatar': user.avatar.url if user.avatar else None,
        'preferred_lang': user.preferred_lang,
        'user_type': user.user_type,
        'is_staff': user.is_staff,
        'is_superuser': user.is_superuser,
        'has_password': user.has_usable_password(),
        'username_change_available': username_change_available(user),
        'telegram': {
            'id': user.telegram_id,
            'username': user.telegram_username or None,
            'photo_url': user.telegram_photo_url or None,
        }
        if user.telegram_id
        else None,
        'primary_organization': {
            'id': str(org.id),
            'name': org.name,
            'slug': getattr(org, 'slug', None),
        }
        if org
        else None,
    }

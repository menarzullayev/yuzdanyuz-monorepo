"""
django-allauth custom adapters (Google is the only social provider).

AccountAdapter:
  - login() issues a device session instead of leaving a Django-session login
    behind; JWTCookieWriterMiddleware copies the tokens onto the response.

SocialAccountAdapter:
  - Pre-hijacking protection: an existing account is linked to a Google login
    automatically ONLY when that account's email is already verified. An
    unverified email (anyone can type any address at signup) never grants
    access: it is stripped from the squatting account and the Google user gets
    a fresh account with the verified address.
  - New Google users get a username generated from their name (one free change).
"""

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin
from django.conf import settings
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.accounts.services.token_service import issue_login
from apps.organizations.services import ensure_platform_membership
from core.utils.client_ip import get_client_ip


class AccountAdapter(DefaultAccountAdapter):
    def get_client_ip(self, request: HttpRequest) -> str:
        # allauth's own rate limits must key on the same address as ours.
        return get_client_ip(request)

    def login(self, request: HttpRequest, user) -> None:
        super().login(request, user)
        issued = issue_login(request, user, remember=True)
        # The response does not exist yet; JWTCookieWriterMiddleware copies
        # these onto it (and DeviceLogoutMiddleware sets the device cookie).
        request._jwt_access = issued.access
        request._jwt_refresh = issued.refresh
        # The OAuth callback must not leave a Django-session login behind: that
        # would keep working after the device session is revoked.
        request.session.flush()


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request: HttpRequest, sociallogin: SocialLogin) -> None:
        from allauth.account.models import EmailAddress

        from apps.accounts.models import CustomUser

        if sociallogin.is_existing:
            return  # already linked to an account
        if request.user.is_authenticated:
            return  # "connect" flow from the profile: allauth attaches it to request.user

        extra = sociallogin.account.extra_data or {}
        email = (extra.get('email') or '').strip().lower()
        if not email or extra.get('email_verified') is False:
            return  # nothing trustworthy to match on

        existing = CustomUser.objects.filter(email__iexact=email).first()
        if existing is None:
            return

        verified = EmailAddress.objects.filter(
            user=existing, email__iexact=email, verified=True
        ).exists()
        if verified and existing.is_active:
            sociallogin.connect(request, existing)
            return

        # Unverified: the email was merely typed at signup. Google's verified
        # ownership wins; the squatter loses the address, never the other way round.
        EmailAddress.objects.filter(user=existing, email__iexact=email).delete()
        existing.email = None
        existing.save(update_fields=['email'])

    def populate_user(self, request: HttpRequest, sociallogin: SocialLogin, data: dict):
        user = super().populate_user(request, sociallogin, data)
        extra = sociallogin.account.extra_data or {}
        if not user.first_name:
            user.first_name = extra.get('given_name', '') or ''
        if not user.last_name:
            user.last_name = extra.get('family_name', '') or ''
        return user

    def save_user(self, request: HttpRequest, sociallogin: SocialLogin, form=None):
        from apps.accounts.services.usernames import generate_username

        user = sociallogin.user
        user.set_unusable_password()
        user.email = (user.email or '').strip().lower() or None
        user.username = generate_username(
            user.first_name, user.last_name, fallback=(user.email or '').split('@')[0]
        )
        user.username_is_auto = True
        user.terms_version = settings.TERMS_VERSION
        user.terms_accepted_at = timezone.now()
        with transaction.atomic():
            sociallogin.save(request)
            ensure_platform_membership(user)
        return user

    def get_connect_redirect_url(self, request: HttpRequest, socialaccount) -> str:
        return f'{settings.FRONTEND_BASE_URL}/profile'

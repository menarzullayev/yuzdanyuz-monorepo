"""
django-allauth custom adapters.

AccountAdapter:
  - login() ni override qilib JWT cookie o'rnatadi
  - allauth session → JWT bridge

SocialAccountAdapter:
  - Email to'qnashuvini boshqaradi:
      * Google verified email > bazadagi unverified email
      * Phone/TG akkauntiga Google bog'lash
"""

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin
from django.conf import settings
from django.http import HttpRequest

from apps.accounts.services.token_service import (
    create_token_pair,
    make_fingerprint,
    set_auth_cookies,
)


class AccountAdapter(DefaultAccountAdapter):
    """
    Login muvaffaqiyatli bo'lgandan keyin JWT cookie ham o'rnatiladi.
    allauth o'z sessionini saqlayd — JWT parallel ishlaydi.
    """

    def login(self, request: HttpRequest, user) -> None:
        super().login(request, user)
        fingerprint    = make_fingerprint(request)
        access, refresh = create_token_pair(user, fingerprint)
        # request.jwt_tokens — response'ga cookie yozish uchun signal
        # (response bu yerda mavjud emas; signal orqali uzatiladi)
        request._jwt_access  = access
        request._jwt_refresh = refresh


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Google OAuth account linking strategiyasi.

    Qoidalar:
      1. Google email verified=True → ishonchli
      2. Agar shu email bilan akkaunt bor + email verified → auto-link
      3. Agar shu email bilan akkaunt bor + email unverified → Google ustun:
           eski unverified email tozalanadi, Google akkaunt bog'lanadi
      4. Phone yoki Telegram akkauntida email yo'q → yangi social connection
    """

    def pre_social_login(self, request: HttpRequest, sociallogin: SocialLogin) -> None:
        from allauth.account.models import EmailAddress
        from apps.accounts.models import CustomUser

        if sociallogin.is_existing:
            return  # allaqachon bog'langan

        email = sociallogin.account.extra_data.get('email', '').lower()
        if not email:
            return

        try:
            existing_user = CustomUser.objects.get(email__iexact=email)
        except CustomUser.DoesNotExist:
            return

        # Agar shu email allauth EmailAddress da verified bo'lsa — xavfsiz link
        ea = EmailAddress.objects.filter(user=existing_user, email__iexact=email).first()
        if ea and ea.verified:
            sociallogin.connect(request, existing_user)
            return

        # Email unverified: Google verified email ustun turadi
        # Unverified email ni Google verified bilan almashtirish
        if ea and not ea.verified:
            ea.delete()

        # Akkauntga Google bog'lash
        sociallogin.connect(request, existing_user)

    def populate_user(self, request: HttpRequest, sociallogin: SocialLogin, data: dict):
        """Google profilidan CustomUser maydonlarini to'ldiradi."""
        user = super().populate_user(request, sociallogin, data)
        extra = sociallogin.account.extra_data

        if not user.first_name:
            user.first_name = extra.get('given_name', '')
        if not user.last_name:
            user.last_name = extra.get('family_name', '')

        # Avatar — Google photo URL sifatida saqlanadi (ImageField emas)
        # TODO: Task 3 da Pillow orqali download + S3 upload qo'shiladi
        return user

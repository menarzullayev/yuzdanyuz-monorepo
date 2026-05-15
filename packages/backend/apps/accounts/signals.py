"""
Django signals for account management
"""

from django.dispatch import receiver
from django.contrib.auth import login
from allauth.socialaccount.signals import pre_social_login
from apps.accounts.services.token_service import create_token_pair, make_fingerprint


@receiver(pre_social_login)
def google_oauth_create_jwt(sender, request, sociallogin, **kwargs):
    """
    Google OAuth login'dan keyin JWT cookie yaratadi.
    allauth session → JWT bridge

    pre_social_login signal ishlaydi, user allaqachon connect qilingan
    so'ng AccountAdapter.login() chaqiriladi
    """
    # pre_social_login signal: bu yerda user hali login qilinmagan
    # AccountAdapter.login() da JWT tokenlar request'ga yoziladi
    # Shu sababli signal handler shuningda faqat ta'kiqlovchi bo'ladi
    pass

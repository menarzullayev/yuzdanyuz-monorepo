"""
Django signals for account management
"""

from allauth.socialaccount.signals import pre_social_login
from django.dispatch import receiver


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

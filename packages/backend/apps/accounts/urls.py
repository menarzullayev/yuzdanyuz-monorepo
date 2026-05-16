from django.urls import path

from apps.accounts.gdpr import GDPRErasureView
from apps.accounts.views.auth import LogoutView, MeView, TelegramAuthView, TokenRefreshView
from apps.accounts.views.linking import (
    GetLinkedAccountsView,
    LinkPhoneView,
    LinkTelegramView,
    UnlinkAuthMethodView,
)
from apps.accounts.views.login_views import (
    EmailLoginView,
    LoginFormView,
    LoginRegisterView,
    LoginView,
    OTPSendHTMXView,
    OTPVerifyHTMXView,
    PhoneLoginFormView,
    PhoneRegisterFormView,
    RegisterFormView,
    RegisterView,
)
from apps.accounts.views.otp import OTPSendView, OTPVerifyView
from apps.accounts.views.telegram_deeplink import (
    BotWebhookView,
    TGDeeplinkCompleteView,
    TGDeeplinkInitView,
    TGDeeplinkStatusView,
)

app_name = 'accounts'

urlpatterns = [
    # Asosiy sahifalar
    path('login/', LoginView.as_view(), name='login'),
    path('login-register/', LoginRegisterView.as_view(), name='login_register'),
    # HTMX form partials
    path('api/forms/login/', LoginFormView.as_view(), name='login_form'),
    path('api/forms/register/', RegisterFormView.as_view(), name='register_form'),
    path('api/forms/phone/login/', PhoneLoginFormView.as_view(), name='phone_login_form'),
    path('api/forms/phone/register/', PhoneRegisterFormView.as_view(), name='phone_register_form'),
    # Form submissions
    path('api/auth/email/', EmailLoginView.as_view(), name='email_login'),
    path('api/auth/register/', RegisterView.as_view(), name='register'),
    path('api/auth/otp/send/htmx/', OTPSendHTMXView.as_view(), name='otp_send_htmx'),
    path('api/auth/otp/verify/htmx/', OTPVerifyHTMXView.as_view(), name='otp_verify_htmx'),
    # Telegram TMA (initData)
    path('api/auth/telegram/', TelegramAuthView.as_view(), name='telegram_auth'),
    # Telegram Deep Link OTP (web sign in)
    path('api/auth/tg/init/', TGDeeplinkInitView.as_view(), name='tg_deeplink_init'),
    path('api/auth/tg/status/', TGDeeplinkStatusView.as_view(), name='tg_deeplink_status'),
    path('api/auth/tg/complete/', TGDeeplinkCompleteView.as_view(), name='tg_deeplink_complete'),
    # Phone OTP (SMS)
    path('api/auth/otp/send/', OTPSendView.as_view(), name='otp_send'),
    path('api/auth/otp/verify/', OTPVerifyView.as_view(), name='otp_verify'),
    # Token management
    path('api/auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/auth/logout/', LogoutView.as_view(), name='logout'),
    # Current user — frontend bootstrap on app load / post-login
    path('api/auth/me/', MeView.as_view(), name='me'),
    # ISSUE-103: GDPR Article 17 erasure
    path('api/auth/gdpr/erasure/', GDPRErasureView.as_view(), name='gdpr_erasure'),
    # Telegram Bot webhook
    path('api/bot/webhook/', BotWebhookView.as_view(), name='bot_webhook'),
    # Account linking
    path('api/linking/phone/', LinkPhoneView.as_view(), name='link_phone'),
    path('api/linking/telegram/', LinkTelegramView.as_view(), name='link_telegram'),
    path('api/linking/unlink/', UnlinkAuthMethodView.as_view(), name='unlink_method'),
    path('api/linking/list/', GetLinkedAccountsView.as_view(), name='list_linked'),
]

from django.urls import path

from apps.accounts.gdpr import GDPRErasureView
from apps.accounts.views.auth import LogoutView, MeView, TelegramAuthView, TokenRefreshView
from apps.accounts.views.credentials import (
    LoginView,
    PasswordChangeView,
    RegisterView,
    UsernameChangeView,
    UsernameCheckView,
)
from apps.accounts.views.linking import (
    GetLinkedAccountsView,
    LinkPhoneView,
    LinkTelegramView,
    UnlinkAuthMethodView,
)
from apps.accounts.views.otp import OTPSendView, OTPVerifyView
from apps.accounts.views.sessions import (
    SessionDetailView,
    SessionListView,
    SessionRevokeOthersView,
)
from apps.accounts.views.telegram_deeplink import (
    BotWebhookView,
    TGDeeplinkCompleteView,
    TGDeeplinkInitView,
    TGDeeplinkStatusView,
)

app_name = 'accounts'

urlpatterns = [
    # Username + password
    path('api/auth/register/', RegisterView.as_view(), name='register'),
    path('api/auth/login/', LoginView.as_view(), name='login'),
    path('api/auth/username/check/', UsernameCheckView.as_view(), name='username_check'),
    path('api/auth/username/', UsernameChangeView.as_view(), name='username_change'),
    path('api/auth/password/change/', PasswordChangeView.as_view(), name='password_change'),
    # Telegram TMA (initData)
    path('api/auth/telegram/', TelegramAuthView.as_view(), name='telegram_auth'),
    # Telegram Deep Link OTP (web sign in)
    path('api/auth/tg/init/', TGDeeplinkInitView.as_view(), name='tg_deeplink_init'),
    path('api/auth/tg/status/', TGDeeplinkStatusView.as_view(), name='tg_deeplink_status'),
    path('api/auth/tg/complete/', TGDeeplinkCompleteView.as_view(), name='tg_deeplink_complete'),
    # Phone OTP (SMS) — behind settings.ENABLE_SMS_OTP
    path('api/auth/otp/send/', OTPSendView.as_view(), name='otp_send'),
    path('api/auth/otp/verify/', OTPVerifyView.as_view(), name='otp_verify'),
    # Token management
    path('api/auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/auth/logout/', LogoutView.as_view(), name='logout'),
    # Active devices
    path('api/auth/sessions/', SessionListView.as_view(), name='sessions'),
    path(
        'api/auth/sessions/revoke-others/',
        SessionRevokeOthersView.as_view(),
        name='sessions_revoke_others',
    ),
    path(
        'api/auth/sessions/<uuid:session_id>/', SessionDetailView.as_view(), name='session_detail'
    ),
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

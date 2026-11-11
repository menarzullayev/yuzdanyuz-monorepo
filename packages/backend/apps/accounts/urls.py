from django.urls import path

from apps.accounts.gdpr import GDPRErasureView
from apps.accounts.views.auth import LogoutView, MeView, TelegramAuthView, TokenRefreshView
from apps.accounts.views.credentials import (
    ForcedPasswordChangeView,
    LoginView,
    PasswordChangeView,
    RegisterView,
    UsernameChangeView,
    UsernameCheckView,
)
from apps.accounts.views.linking import (
    LinkedAccountsView,
    LinkTelegramStartView,
    TelegramLinkView,
    UnlinkGoogleView,
)
from apps.accounts.views.onboarding import (
    OnboardingOptionsView,
    OnboardingView,
    OrgConsentRevokeView,
    OrgInviteView,
)
from apps.accounts.views.otp import OTPSendView, OTPVerifyView
from apps.accounts.views.recovery import (
    AdminQueueRequestView,
    OrgAdminResetView,
    RecoveryConfirmView,
    TelegramRecoveryView,
)
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
from apps.accounts.views.two_factor import (
    BackupCodesRegenerateView,
    TwoFactorConfirmView,
    TwoFactorDisableView,
    TwoFactorSetupView,
    TwoFactorStatusView,
)

app_name = 'accounts'

urlpatterns = [
    # Username + password
    path('api/auth/register/', RegisterView.as_view(), name='register'),
    path('api/auth/login/', LoginView.as_view(), name='login'),
    path('api/auth/username/check/', UsernameCheckView.as_view(), name='username_check'),
    path('api/auth/username/', UsernameChangeView.as_view(), name='username_change'),
    path('api/auth/password/change/', PasswordChangeView.as_view(), name='password_change'),
    path(
        'api/auth/password/change-required/',
        ForcedPasswordChangeView.as_view(),
        name='password_change_required',
    ),
    # Password recovery (no email)
    path('api/auth/recovery/telegram/', TelegramRecoveryView.as_view(), name='recovery_telegram'),
    path('api/auth/recovery/confirm/', RecoveryConfirmView.as_view(), name='recovery_confirm'),
    path('api/auth/recovery/request/', AdminQueueRequestView.as_view(), name='recovery_request'),
    path('api/auth/recovery/org-reset/', OrgAdminResetView.as_view(), name='recovery_org_reset'),
    # Two-factor authentication
    path('api/auth/2fa/', TwoFactorStatusView.as_view(), name='2fa_status'),
    path('api/auth/2fa/setup/', TwoFactorSetupView.as_view(), name='2fa_setup'),
    path('api/auth/2fa/confirm/', TwoFactorConfirmView.as_view(), name='2fa_confirm'),
    path('api/auth/2fa/disable/', TwoFactorDisableView.as_view(), name='2fa_disable'),
    path(
        'api/auth/2fa/backup-codes/regenerate/',
        BackupCodesRegenerateView.as_view(),
        name='2fa_backup_regenerate',
    ),
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
    # Onboarding profile
    path('api/auth/onboarding/', OnboardingView.as_view(), name='onboarding'),
    path(
        'api/auth/onboarding/options/', OnboardingOptionsView.as_view(), name='onboarding_options'
    ),
    path('api/auth/onboarding/org-invite/', OrgInviteView.as_view(), name='onboarding_org_invite'),
    path(
        'api/auth/onboarding/org-consent/<uuid:org_id>/',
        OrgConsentRevokeView.as_view(),
        name='onboarding_org_consent',
    ),
    # Account linking (profile)
    path('api/auth/linked/', LinkedAccountsView.as_view(), name='linked'),
    path(
        'api/auth/linked/telegram/',
        TelegramLinkView.as_view(),
        name='link_telegram',
    ),
    path(
        'api/auth/linked/telegram/start/',
        LinkTelegramStartView.as_view(),
        name='link_telegram_start',
    ),
    path('api/auth/linked/google/', UnlinkGoogleView.as_view(), name='unlink_google'),
]

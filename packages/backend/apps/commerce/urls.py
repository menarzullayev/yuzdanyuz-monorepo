"""Task 7 — Commerce (Wallet/Subscription/Affiliate) URLs."""

from django.urls import path

from . import views

app_name = 'commerce'

urlpatterns = [
    # Wallet
    path('wallet/', views.WalletView.as_view(), name='wallet'),
    path(
        'wallet/transactions/', views.WalletTransactionsView.as_view(), name='wallet-transactions'
    ),
    path('wallet/topup/', views.TopUpView.as_view(), name='wallet-topup'),
    path('wallet/spend/', views.SpendView.as_view(), name='wallet-spend'),
    # Webhooks
    path('wallet/webhooks/payme/', views.PaymeWebhookView.as_view(), name='payme-webhook'),
    path('wallet/webhooks/click/', views.ClickWebhookView.as_view(), name='click-webhook'),
    # Subscriptions
    path('subscriptions/plans/', views.SubscriptionPlansView.as_view(), name='sub-plans'),
    path('subscriptions/current/', views.CurrentSubscriptionView.as_view(), name='sub-current'),
    path('subscriptions/subscribe/', views.SubscribeView.as_view(), name='sub-subscribe'),
    path('subscriptions/cancel/', views.CancelSubscriptionView.as_view(), name='sub-cancel'),
    # Affiliate
    path('affiliate/code/', views.AffiliateCodeView.as_view(), name='affiliate-code'),
    path('affiliate/stats/', views.AffiliateStatsView.as_view(), name='affiliate-stats'),
]

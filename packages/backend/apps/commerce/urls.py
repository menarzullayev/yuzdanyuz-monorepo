"""Task 7 — Commerce (Wallet/Subscription/Affiliate) URLs."""

from django.urls import path

from . import billing_views as bv
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
    # Monetisation: B2C tariffs, promo, B2B exam keys
    path('billing/catalog/', bv.CatalogView.as_view(), name='billing-catalog'),
    path('billing/me/', bv.MyBillingView.as_view(), name='billing-me'),
    path('billing/access/', bv.AccessCheckView.as_view(), name='billing-access'),
    path('billing/promo/validate/', bv.PromoValidateView.as_view(), name='billing-promo-validate'),
    path('billing/checkout/', bv.CheckoutView.as_view(), name='billing-checkout'),
    path('billing/admin/keys/grant/', bv.AdminGrantKeysView.as_view(), name='billing-admin-grant'),
    path('org/keys/summary/', bv.OrgKeySummaryView.as_view(), name='orgkeys-summary'),
    path('org/keys/', bv.OrgKeyListView.as_view(), name='orgkeys-list'),
    path('org/keys/purchase/', bv.OrgKeyPurchaseView.as_view(), name='orgkeys-purchase'),
    path('org/keys/distribute/', bv.OrgKeyDistributeView.as_view(), name='orgkeys-distribute'),
    path('org/keys/revoke/', bv.OrgKeyRevokeView.as_view(), name='orgkeys-revoke'),
    path('exam-keys/mine/', bv.MyKeysView.as_view(), name='examkeys-mine'),
    path('exam-keys/redeem/', bv.RedeemKeyView.as_view(), name='examkeys-redeem'),
]

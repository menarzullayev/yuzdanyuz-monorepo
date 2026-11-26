"""OpenAPI shapes for the wallet, subscription and affiliate endpoints (audit D04).

The views build plain dicts; these serializers only describe them for the schema.
"""

from drf_spectacular.utils import OpenApiResponse, inline_serializer
from rest_framework import serializers as s

_ERROR = OpenApiResponse(
    description='Error envelope: {success, error{code, message, details}, detail}'
)

wallet = inline_serializer(
    'Wallet',
    {
        'balance_coins': s.IntegerField(),
        'pending_cash_uzs': s.DecimalField(max_digits=14, decimal_places=2),
        'updated_at': s.DateTimeField(),
    },
)

wallet_transactions = inline_serializer(
    'WalletTransactions',
    {
        'count': s.IntegerField(),
        'transactions': inline_serializer(
            'WalletTransaction',
            {
                'id': s.UUIDField(),
                'kind': s.CharField(),
                'coins_delta': s.IntegerField(),
                'cash_uzs_delta': s.DecimalField(max_digits=14, decimal_places=2),
                'balance_after_coins': s.IntegerField(),
                'description': s.CharField(),
                'created_at': s.DateTimeField(),
            },
            many=True,
        ),
    },
)

topup_response = inline_serializer(
    'TopUpIntent',
    {
        'intent_id': s.UUIDField(),
        'checkout_url': s.URLField(),
        'amount_uzs': s.DecimalField(max_digits=12, decimal_places=2),
        'coins_to_credit': s.IntegerField(),
    },
)

spend_response = inline_serializer(
    'SpendResult', {'transaction_id': s.UUIDField(), 'balance_after_coins': s.IntegerField()}
)

_PLAN = (
    'SubscriptionPlan',
    {
        'id': s.UUIDField(),
        'name': s.CharField(),
        'slug': s.SlugField(),
        'description': s.CharField(),
        'billing_period': s.CharField(),
        'pricing_model': s.CharField(),
        'price_uzs': s.DecimalField(max_digits=12, decimal_places=2),
        'max_users': s.IntegerField(allow_null=True),
        'features': s.JSONField(),
    },
)

plans = inline_serializer('SubscriptionPlans', {'plans': inline_serializer(*_PLAN, many=True)})

_SUBSCRIPTION = (
    'OrganizationSubscription',
    {
        'id': s.UUIDField(),
        'plan': s.CharField(),
        'plan_slug': s.SlugField(),
        'status': s.CharField(),
        'started_at': s.DateTimeField(allow_null=True),
        'current_period_started_at': s.DateTimeField(allow_null=True),
        'current_period_ends_at': s.DateTimeField(allow_null=True),
        'auto_renew': s.BooleanField(),
        'cancelled_at': s.DateTimeField(allow_null=True),
    },
)

subscription_wrapper = inline_serializer(
    'SubscriptionEnvelope', {'subscription': inline_serializer(*_SUBSCRIPTION)}
)

current_subscription = inline_serializer(
    'CurrentSubscription', {'subscription': inline_serializer(*_SUBSCRIPTION, allow_null=True)}
)

subscribe_request = inline_serializer(
    'SubscribeRequest',
    {
        'plan_slug': s.SlugField(),
        'payment_provider': s.ChoiceField(choices=('payme', 'click'), default='payme'),
    },
)

cancel_request = inline_serializer(
    'CancelSubscriptionRequest', {'reason': s.CharField(required=False, allow_blank=True)}
)

affiliate_code = inline_serializer(
    'AffiliateCode',
    {'code': s.CharField(), 'is_withdrawable': s.BooleanField(), 'created_at': s.DateTimeField()},
)

affiliate_stats = inline_serializer(
    'AffiliateStats',
    {
        'total_referrals': s.IntegerField(),
        'total_coin_earned': s.IntegerField(),
        'pending_cash_uzs': s.DecimalField(max_digits=14, decimal_places=2),
        'is_withdrawable': s.BooleanField(),
    },
)

webhook_request = inline_serializer(
    'PaymentWebhookPayload',
    {'payload': s.JSONField(help_text='Provider-specific body (Payme JSON-RPC / Click form).')},
)

ERRORS = {400: _ERROR, 401: _ERROR, 403: _ERROR}

subscribe_response = inline_serializer(
    'SubscribeCheckout',
    {
        'subscription_id': s.UUIDField(),
        'intent_id': s.UUIDField(),
        'checkout_url': s.URLField(),
        'amount_uzs': s.DecimalField(max_digits=12, decimal_places=2),
        'plan': s.CharField(),
    },
)

webhook_ack = OpenApiResponse(
    description='Provider-specific acknowledgement (Payme JSON-RPC result or Click JSON).'
)

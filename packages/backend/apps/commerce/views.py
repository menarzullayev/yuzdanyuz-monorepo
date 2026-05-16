"""
Task 7 — Billing/Wallet REST API.

Endpoints:
  GET  /api/wallet/                              user wallet balance
  GET  /api/wallet/transactions/                 audit trail
  POST /api/wallet/topup/                         init payment (Payme/Click)
  POST /api/wallet/spend/                         spend Coin (premium feature)

  POST /api/wallet/webhooks/payme/               Payme callback
  POST /api/wallet/webhooks/click/               Click callback

  GET  /api/subscriptions/plans/                 list available plans
  GET  /api/subscriptions/current/               current org subscription
  POST /api/subscriptions/subscribe/             subscribe org to plan
  POST /api/subscriptions/cancel/                cancel current sub

  GET  /api/affiliate/code/                      get/create my referral code
  GET  /api/affiliate/stats/                     my referrals + earnings

Auth: IsAuthenticated default. Subscription endpoint'lari org admin only.
"""

import logging
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import affiliate_service, payment_providers, subscription_service, wallet_service
from .models import (
    OrganizationSubscription,
    PaymentIntent,
    Referral,
    SubscriptionPlan,
)

logger = logging.getLogger(__name__)

# Conversion: 1 Coin = 100 UZS (default exchange rate)
COIN_TO_UZS_RATE = 100


# ─── Wallet ──────────────────────────────────────────────────────────────────


class WalletView(APIView):
    def get(self, request):
        wallet = wallet_service.get_or_create_wallet(request.user)
        return Response(
            {
                'balance_coins': wallet.balance_coins,
                'pending_cash_uzs': str(wallet.pending_cash_uzs),
                'updated_at': wallet.updated_at,
            }
        )


class WalletTransactionsView(APIView):
    def get(self, request):
        wallet = wallet_service.get_or_create_wallet(request.user)
        # ISSUE-101: explicit ordering bilan -created_at index'idan foydalanish.
        # FK access yo'q (faqat scalar field'lar), shuning uchun select_related kerakmas.
        txs = list(wallet.transactions.order_by('-created_at')[:100])
        return Response(
            {
                'count': len(txs),
                'transactions': [
                    {
                        'id': str(t.id),
                        'kind': t.kind,
                        'coins_delta': t.coins_delta,
                        'cash_uzs_delta': str(t.cash_uzs_delta),
                        'balance_after_coins': t.balance_after_coins,
                        'description': t.description,
                        'created_at': t.created_at,
                    }
                    for t in txs
                ],
            }
        )


class TopUpView(APIView):
    """POST /api/wallet/topup/  body: {provider: 'payme'|'click', amount_uzs: int}"""

    def post(self, request):
        provider_name = request.data.get('provider', 'payme')
        try:
            amount_uzs = Decimal(str(request.data.get('amount_uzs', 0)))
        except (TypeError, ValueError):
            raise ValidationError({'amount_uzs': 'invalid'}) from None
        if amount_uzs <= 0:
            raise ValidationError({'amount_uzs': 'must be positive'})
        if provider_name not in ('payme', 'click'):
            raise ValidationError({'provider': "'payme' yoki 'click'"})

        coins = int(amount_uzs / COIN_TO_UZS_RATE)
        intent = PaymentIntent.objects.create(
            user=request.user,
            provider=provider_name,
            amount_uzs=amount_uzs,
            coins_to_credit=coins,
        )
        provider = payment_providers.get_provider(provider_name)
        result = provider.initiate_charge(intent)
        return Response(
            {
                'intent_id': str(intent.id),
                'checkout_url': result['checkout_url'],
                'provider_tx_id': result['provider_tx_id'],
                'amount_uzs': str(amount_uzs),
                'coins_to_credit': coins,
            },
            status=status.HTTP_201_CREATED,
        )


class SpendView(APIView):
    """POST /api/wallet/spend/  body: {coins: int, description: str}"""

    def post(self, request):
        try:
            coins = int(request.data.get('coins', 0))
        except (TypeError, ValueError):
            raise ValidationError({'coins': 'invalid'}) from None
        if coins <= 0:
            raise ValidationError({'coins': 'must be positive'})

        wallet = wallet_service.get_or_create_wallet(request.user)
        try:
            tx = wallet_service.spend(
                wallet, coins, description=request.data.get('description', '')
            )
        except wallet_service.InsufficientFunds as e:
            return Response({'detail': str(e)}, status=status.HTTP_402_PAYMENT_REQUIRED)

        return Response(
            {
                'transaction_id': str(tx.id),
                'balance_after_coins': tx.balance_after_coins,
            }
        )


# ─── Webhooks (Payme/Click) ──────────────────────────────────────────────────


def _process_webhook(provider_name: str, request) -> Response:
    provider = payment_providers.get_provider(provider_name)
    payload = request.data if isinstance(request.data, dict) else {}
    signature = request.headers.get('X-Signature', '')

    if not provider.verify_webhook_signature(payload, signature):
        return Response({'detail': 'Invalid signature'}, status=status.HTTP_401_UNAUTHORIZED)

    parsed = provider.parse_webhook(payload)
    provider_tx_id = parsed.get('provider_tx_id')
    if not provider_tx_id:
        return Response({'detail': 'Missing provider_tx_id'}, status=400)

    try:
        intent = PaymentIntent.objects.get(provider=provider_name, provider_tx_id=provider_tx_id)
    except PaymentIntent.DoesNotExist:
        return Response({'detail': 'Intent not found'}, status=404)

    if intent.status == PaymentIntent.Status.SUCCEEDED:
        # Idempotent — already processed
        return Response({'detail': 'Already processed'}, status=200)

    # ISSUE-104: business metric
    from core.metrics import PAYMENTS_COMPLETED

    if parsed.get('status') == 'succeeded':
        # Credit user wallet
        wallet = wallet_service.get_or_create_wallet(intent.user)
        wallet_service.top_up(
            wallet,
            intent.coins_to_credit,
            payment_intent=intent,
            description=f'Top-up via {provider_name}',
        )
        intent.status = PaymentIntent.Status.SUCCEEDED
        intent.save(update_fields=['status', 'updated_at'])

        # Subscription activate (agar bog'langan bo'lsa)
        if intent.target_subscription_plan and intent.target_organization:
            try:
                sub = OrganizationSubscription.objects.get(last_payment_intent=intent)
                subscription_service.activate(sub)
            except OrganizationSubscription.DoesNotExist:
                pass
        PAYMENTS_COMPLETED.labels(provider=provider_name, status='succeeded').inc()
    else:
        intent.status = PaymentIntent.Status.FAILED
        intent.error_message = parsed.get('error', '')[:500]
        intent.save(update_fields=['status', 'error_message', 'updated_at'])
        PAYMENTS_COMPLETED.labels(provider=provider_name, status='failed').inc()

    return Response({'detail': 'OK', 'intent_status': intent.status})


@permission_classes([AllowAny])
class PaymeWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        return _process_webhook('payme', request)


@permission_classes([AllowAny])
class ClickWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        return _process_webhook('click', request)


# ─── Subscriptions ───────────────────────────────────────────────────────────


class SubscriptionPlansView(APIView):
    def get(self, request):
        plans = SubscriptionPlan.objects.filter(is_active=True)
        return Response(
            {
                'plans': [
                    {
                        'id': str(p.id),
                        'name': p.name,
                        'slug': p.slug,
                        'description': p.description,
                        'billing_period': p.billing_period,
                        'pricing_model': p.pricing_model,
                        'price_uzs': str(p.price_uzs),
                        'max_users': p.max_users,
                        'features': p.features,
                    }
                    for p in plans
                ]
            }
        )


def _is_org_admin(user, org) -> bool:
    if user.is_superuser or user.is_staff:
        return True
    return user.memberships.filter(
        organization=org, role__name__in=['owner', 'admin'], status='active'
    ).exists()


class CurrentSubscriptionView(APIView):
    """GET /api/subscriptions/current/  (org tenant context'idan)"""

    def get(self, request):
        org = getattr(request, 'org', None)
        if org is None:
            return Response({'detail': "Org context yo'q"}, status=400)
        sub = subscription_service.get_active_subscription(org)
        if sub is None:
            return Response({'subscription': None})
        return Response({'subscription': _serialize_subscription(sub)})


class SubscribeView(APIView):
    """POST /api/subscriptions/subscribe/  body: {plan_slug, payment_provider}"""

    def post(self, request):
        org = getattr(request, 'org', None)
        if org is None:
            return Response({'detail': "Org context yo'q"}, status=400)
        if not _is_org_admin(request.user, org):
            raise PermissionDenied('Faqat org admin/owner subscribe qila oladi.')

        plan_slug = request.data.get('plan_slug')
        provider_name = request.data.get('payment_provider', 'payme')
        plan = get_object_or_404(SubscriptionPlan, slug=plan_slug, is_active=True)

        # Per-seat: hozircha active_user_count = membership active count
        from apps.organizations.models import Membership

        active_users = Membership.objects.filter(organization=org, status='active').count()
        amount = plan.calculate_charge(active_user_count=active_users)
        coins = 0  # Subscription emas, top-up emas

        intent = PaymentIntent.objects.create(
            user=request.user,
            provider=provider_name,
            amount_uzs=amount,
            coins_to_credit=coins,
            target_subscription_plan=plan,
            target_organization=org,
        )
        provider = payment_providers.get_provider(provider_name)
        checkout = provider.initiate_charge(intent)

        sub = subscription_service.subscribe(org, plan, payment_intent=intent)

        return Response(
            {
                'subscription_id': str(sub.id),
                'intent_id': str(intent.id),
                'checkout_url': checkout['checkout_url'],
                'amount_uzs': str(amount),
                'plan': plan.name,
            },
            status=status.HTTP_201_CREATED,
        )


class CancelSubscriptionView(APIView):
    def post(self, request):
        org = getattr(request, 'org', None)
        if org is None:
            return Response({'detail': "Org context yo'q"}, status=400)
        if not _is_org_admin(request.user, org):
            raise PermissionDenied('Faqat org admin/owner cancel qila oladi.')
        sub = subscription_service.get_active_subscription(org)
        if sub is None:
            return Response({'detail': "Aktiv sub yo'q"}, status=404)
        reason = request.data.get('reason', '')
        subscription_service.cancel(sub, reason=reason)
        return Response({'subscription': _serialize_subscription(sub)})


def _serialize_subscription(sub: OrganizationSubscription) -> dict:
    return {
        'id': str(sub.id),
        'plan': sub.plan.name,
        'plan_slug': sub.plan.slug,
        'status': sub.status,
        'started_at': sub.started_at,
        'current_period_started_at': sub.current_period_started_at,
        'current_period_ends_at': sub.current_period_ends_at,
        'auto_renew': sub.auto_renew,
        'cancelled_at': sub.cancelled_at,
    }


# ─── Affiliate ───────────────────────────────────────────────────────────────


class AffiliateCodeView(APIView):
    def get(self, request):
        code = affiliate_service.get_or_create_code(request.user)
        return Response(
            {
                'code': code.code,
                'is_withdrawable': code.is_withdrawable,
                'created_at': code.created_at,
            }
        )


class AffiliateStatsView(APIView):
    def get(self, request):
        wallet = wallet_service.get_or_create_wallet(request.user)
        # ISSUE-101: 2 ta alohida query (count + iterate-sum) o'rniga bitta aggregate.
        stats = Referral.objects.filter(inviter=request.user).aggregate(
            total_referrals=Count('id'),
            total_coin_earned=Coalesce(Sum('coin_reward'), 0),
        )
        return Response(
            {
                'total_referrals': stats['total_referrals'],
                'total_coin_earned': stats['total_coin_earned'],
                'pending_cash_uzs': str(wallet.pending_cash_uzs),
                'is_withdrawable': getattr(request.user.referral_code, 'is_withdrawable', False)
                if hasattr(request.user, 'referral_code')
                else False,
            }
        )

"""
Task 7 — Billing/Wallet/Subscription modellari.

Architecture (docs/milliy_sertifikat_django.md, Bosqich 6+11+24):
  Gibrid Billing — Hamyon (B2C konversiya) + Direct subscription (B2B) +
  Affiliate viral loop.

Models:
  - Wallet                     — per-user Sertifikat Coin balance
  - WalletTransaction          — audit trail (debit/credit, kind)
  - PaymentIntent              — Payme/Click charge attempt
  - SubscriptionPlan           — flexible (monthly/yearly/lifetime, flat/per_seat)
  - OrganizationSubscription   — org-level active subscription
  - ReferralCode               — user'ning referral code
  - Referral                   — invitation tracking
"""

import secrets
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.mixins import SoftDeleteMixin

# ── 1. Wallet + WalletTransaction ─────────────────────────────────────────────


class Wallet(models.Model):
    """
    Per-user Sertifikat Coin balance. SELECT FOR UPDATE bilan atomic ops.

    Coin = integer. Top-up: 10000 UZS → 100 Coin (1 Coin = 100 UZS, default rate).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='wallet',
    )
    balance_coins = models.PositiveIntegerField(
        default=0, verbose_name=_('Sertifikat Coin balansi')
    )
    pending_cash_uzs = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text=_("Affiliate withdraw'ga tayyor naqd UZS"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Wallet')
        verbose_name_plural = _('Wallets')

    def __str__(self):
        return f'{self.user} wallet ({self.balance_coins} Coin)'


class WalletTransaction(SoftDeleteMixin, models.Model):
    """Audit trail. Immutable: har wallet o'zgarishi bu yerda yoziladi.

    ISSUE-103: soft-delete bilan — GDPR erasure'da user PII anonimlashtirildi,
    lekin tx ID + amount + kind + timestamp saqlanadi (UZ buxgalteriya 5 yil).
    """

    pii_fields = ('description',)  # description'da user-typed text bo'lishi mumkin

    class Kind(models.TextChoices):
        TOPUP = 'topup', _("To'ldirish (Payme/Click)")
        SPEND = 'spend', _('Sarflash (premium feature)')
        REFUND = 'refund', _('Qaytarish')
        REFERRAL_BONUS = 'referral_bonus', _('Referral bonus')
        AFFILIATE_PAYOUT = 'affiliate_payout', _('Affiliate cash-out (UZS)')
        ADJUSTMENT = 'adjustment', _('Admin sozlash')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='transactions')
    kind = models.CharField(max_length=24, choices=Kind.choices, db_index=True)

    # Coin delta (signed: +ve = credit, -ve = debit)
    coins_delta = models.IntegerField(default=0)
    cash_uzs_delta = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    # Snapshot post-update — audit clarity
    balance_after_coins = models.PositiveIntegerField()
    balance_after_cash_uzs = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00')
    )

    description = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    payment_intent = models.ForeignKey(
        'PaymentIntent',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='wallet_transactions',
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _('Wallet Transaction')
        verbose_name_plural = _('Wallet Transactions')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['wallet', '-created_at']),
            models.Index(fields=['kind', '-created_at']),
            # ISSUE-306: composite (wallet, kind, -created_at) — filtered history
            models.Index(fields=['wallet', 'kind', '-created_at'], name='wt_wallet_kind_idx'),
        ]

    def __str__(self):
        sign = '+' if self.coins_delta >= 0 else ''
        return f'{self.wallet.user} {sign}{self.coins_delta} Coin ({self.kind})'


# ── 2. PaymentIntent ─────────────────────────────────────────────────────────


class PaymentIntent(SoftDeleteMixin, models.Model):
    """
    Payme/Click charge initiatsiyasidan webhook qaytishigacha bo'lgan
    holatni saqlaydi. Idempotent (provider, provider_tx_id) unique.

    ISSUE-103: soft-delete + PII redaction. Provider tx_id va amount saqlanadi
    (financial reconciliation), faqat metadata redacted.
    """

    pii_fields = ('metadata',)  # JSON: provider request/response (user IP, headers)

    class Provider(models.TextChoices):
        PAYME = 'payme', _('Payme')
        CLICK = 'click', _('Click')
        STUB = 'stub', _('Stub (dev/test)')

    class Status(models.TextChoices):
        CREATED = 'created', _('Yaratilgan')
        PROCESSING = 'processing', _('Ishlanmoqda')
        SUCCEEDED = 'succeeded', _('Muvaffaqiyatli')
        FAILED = 'failed', _('Muvaffaqiyatsiz')
        CANCELLED = 'cancelled', _('Bekor qilingan')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='payment_intents',
    )
    provider = models.CharField(max_length=8, choices=Provider.choices, db_index=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.CREATED, db_index=True
    )

    amount_uzs = models.DecimalField(max_digits=12, decimal_places=2)
    coins_to_credit = models.PositiveIntegerField(
        help_text=_("Muvaffaqiyatli bo'lsa user wallet'iga shu Coin qo'shiladi")
    )

    # Provider tx ID (Payme transaction ID, Click payment_id) — webhook'dan keladi
    provider_tx_id = models.CharField(max_length=128, blank=True, db_index=True)

    target_subscription_plan = models.ForeignKey(
        'SubscriptionPlan',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    target_organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    metadata = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Payment Intent')
        verbose_name_plural = _('Payment Intents')
        ordering = ['-created_at']
        unique_together = [('provider', 'provider_tx_id')]
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['status', '-created_at']),
        ]

    # ISSUE-205: state machine
    #   CREATED ──start_processing()──► PROCESSING
    #           │
    #           ├──cancel()──► CANCELLED (user yoki timeout)
    #           │
    #           PROCESSING ──mark_succeeded()──► SUCCEEDED (webhook OK)
    #                      └──mark_failed()──► FAILED (webhook error)
    _ALLOWED_TRANSITIONS = {
        Status.CREATED: {Status.PROCESSING, Status.CANCELLED, Status.FAILED},
        Status.PROCESSING: {Status.SUCCEEDED, Status.FAILED, Status.CANCELLED},
        Status.SUCCEEDED: set(),
        Status.FAILED: set(),
        Status.CANCELLED: set(),
    }

    def __str__(self):
        return f'{self.user} {self.amount_uzs} UZS ({self.provider}/{self.status})'

    def _validate_transition(self, target: 'PaymentIntent.Status') -> None:
        allowed = self._ALLOWED_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            raise ValueError(
                f'Invalid state transition: {self.status} → {target} (allowed: {sorted(allowed)})'
            )

    def start_processing(self) -> None:
        """CREATED → PROCESSING. Provider charge boshlandi."""
        self._validate_transition(self.Status.PROCESSING)
        self.status = self.Status.PROCESSING
        self.save(update_fields=['status', 'updated_at'])

    def mark_succeeded(self, *, provider_tx_id: str = '') -> None:
        """{CREATED, PROCESSING} → SUCCEEDED. Webhook valid signature bilan."""
        self._validate_transition(self.Status.SUCCEEDED)
        self.status = self.Status.SUCCEEDED
        if provider_tx_id:
            self.provider_tx_id = provider_tx_id
        self.save(update_fields=['status', 'provider_tx_id', 'updated_at'])

    def mark_failed(self, *, error_message: str = '') -> None:
        """{CREATED, PROCESSING} → FAILED. Provider rad etdi yoki timeout."""
        self._validate_transition(self.Status.FAILED)
        self.status = self.Status.FAILED
        if error_message:
            self.error_message = error_message
        self.save(update_fields=['status', 'error_message', 'updated_at'])

    def cancel(self) -> None:
        """{CREATED, PROCESSING} → CANCELLED. User yoki sistema bekor qildi."""
        self._validate_transition(self.Status.CANCELLED)
        self.status = self.Status.CANCELLED
        self.save(update_fields=['status', 'updated_at'])


# ── 3. SubscriptionPlan + OrganizationSubscription ───────────────────────────


class SubscriptionPlan(models.Model):
    """
    Flexible plan tuzilishi:
      billing_period: monthly / yearly / lifetime
      pricing_model:
        flat       — org bir marta to'laydi, barcha o'quvchilar bepul
        per_seat   — har faol o'quvchi uchun price_uzs * count
    """

    class BillingPeriod(models.TextChoices):
        MONTHLY = 'monthly', _('Oylik')
        YEARLY = 'yearly', _('Yillik')
        LIFETIME = 'lifetime', _('Butun umr')

    class PricingModel(models.TextChoices):
        FLAT = 'flat', _('Belgilangan summa (org)')
        PER_SEAT = 'per_seat', _("O'quvchi soni bo'yicha")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80, unique=True)
    description = models.TextField(blank=True)

    billing_period = models.CharField(max_length=10, choices=BillingPeriod.choices)
    pricing_model = models.CharField(max_length=10, choices=PricingModel.choices)

    price_uzs = models.DecimalField(max_digits=12, decimal_places=2)

    max_users = models.PositiveIntegerField(
        null=True, blank=True, help_text=_('Cheksiz uchun null')
    )
    features = models.JSONField(
        default=dict,
        help_text=_("Misol: {'ai_diagnostic': true, 'analytics': true}"),
    )

    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Subscription Plan')
        verbose_name_plural = _('Subscription Plans')
        ordering = ['sort_order', 'price_uzs']

    def __str__(self):
        return f'{self.name} ({self.billing_period}, {self.pricing_model})'

    def calculate_charge(self, *, active_user_count: int = 0) -> Decimal:
        """Plan turidan kelib chiqib joriy charge'ni hisoblash."""
        if self.pricing_model == self.PricingModel.PER_SEAT:
            return self.price_uzs * Decimal(active_user_count)
        return self.price_uzs


class OrganizationSubscription(models.Model):
    """Org'ning aktiv subscription'i."""

    class Status(models.TextChoices):
        TRIALING = 'trialing', _('Trial davri')
        ACTIVE = 'active', _('Faol')
        PAST_DUE = 'past_due', _("To'lov muddati o'tgan")
        CANCELLED = 'cancelled', _('Bekor qilingan')
        EXPIRED = 'expired', _('Muddati tugagan')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='subscriptions',
    )
    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.PROTECT, related_name='subscriptions'
    )

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.TRIALING, db_index=True
    )

    started_at = models.DateTimeField(auto_now_add=True)
    current_period_started_at = models.DateTimeField()
    # Lifetime: null
    current_period_ends_at = models.DateTimeField(null=True, blank=True, db_index=True)

    auto_renew = models.BooleanField(default=True)

    last_payment_intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Organization Subscription')
        verbose_name_plural = _('Organization Subscriptions')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['status', 'current_period_ends_at']),
            # ISSUE-306: partial index — auto_renew_subscriptions task'i
            # ACTIVE/TRIALING + auto_renew=True row'larni scan qiladi.
            # Full index hajmi 80%+ kerakmas → partial 5-10x kichik, tezroq.
            models.Index(
                fields=['current_period_ends_at'],
                name='sub_active_renewable_idx',
                condition=models.Q(
                    status__in=['active', 'trialing'],
                    auto_renew=True,
                ),
            ),
        ]

    # ISSUE-205: state machine
    #   TRIALING ──activate()──► ACTIVE ──mark_past_due()──► PAST_DUE
    #                            │              │
    #                            │              └──reinstate()──► ACTIVE
    #                            │              └──cancel()──► CANCELLED
    #                            │              └──expire()──► EXPIRED
    #                            ├──cancel()──► CANCELLED
    #                            └──expire()──► EXPIRED
    _ALLOWED_TRANSITIONS = {
        Status.TRIALING: {Status.ACTIVE, Status.CANCELLED, Status.EXPIRED},
        Status.ACTIVE: {Status.PAST_DUE, Status.CANCELLED, Status.EXPIRED},
        Status.PAST_DUE: {Status.ACTIVE, Status.CANCELLED, Status.EXPIRED},
        Status.CANCELLED: set(),
        Status.EXPIRED: set(),
    }

    def __str__(self):
        return f'{self.organization} → {self.plan.name} ({self.status})'

    def _validate_transition(self, target: 'OrganizationSubscription.Status') -> None:
        allowed = self._ALLOWED_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            raise ValueError(
                f'Invalid state transition: {self.status} → {target} (allowed: {sorted(allowed)})'
            )

    def activate(self) -> None:
        """{TRIALING, PAST_DUE} → ACTIVE. Trial converted yoki PAST_DUE to'lov keldi."""
        self._validate_transition(self.Status.ACTIVE)
        self.status = self.Status.ACTIVE
        self.save(update_fields=['status', 'updated_at'])

    def mark_past_due(self) -> None:
        """ACTIVE → PAST_DUE. Auto-renew charge fail bo'ldi, grace period boshlanadi."""
        self._validate_transition(self.Status.PAST_DUE)
        self.status = self.Status.PAST_DUE
        self.save(update_fields=['status', 'updated_at'])

    def cancel(self, *, reason: str = '') -> None:
        """Har qanday non-terminal → CANCELLED (user yoki admin)."""
        self._validate_transition(self.Status.CANCELLED)
        from django.utils import timezone

        self.status = self.Status.CANCELLED
        self.cancelled_at = timezone.now()
        if reason:
            self.cancellation_reason = reason
        self.save(update_fields=['status', 'cancelled_at', 'cancellation_reason', 'updated_at'])

    def expire(self) -> None:
        """Har qanday non-terminal → EXPIRED. Period tugadi, auto-renew o'chiq."""
        self._validate_transition(self.Status.EXPIRED)
        self.status = self.Status.EXPIRED
        self.save(update_fields=['status', 'updated_at'])


# ── 4. Referral system ───────────────────────────────────────────────────────


def _gen_code() -> str:
    """Returns 8-character alphanumeric code (no '-' or '_' to avoid confusion)."""
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    return ''.join(secrets.choice(alphabet) for _ in range(8))


class ReferralCode(models.Model):
    """User'ning referral code (signup'da ishlatiladigan)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='referral_code',
    )
    code = models.CharField(max_length=16, unique=True, default=_gen_code)
    is_withdrawable = models.BooleanField(
        default=False,
        help_text=_('50+ referrals → naqd UZS yechib olish ochiladi'),
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Referral Code')

    def __str__(self):
        return f'{self.user} → {self.code}'


class Referral(models.Model):
    """Signup voqeasi: invited_user invited_by'ning code'ini ishlatib qo'shilgan."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inviter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_referrals',
    )
    invited = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='joined_referral',
    )
    coin_reward = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _('Referral')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.inviter} → {self.invited}'

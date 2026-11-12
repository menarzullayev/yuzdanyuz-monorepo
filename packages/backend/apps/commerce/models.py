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

Monetisation (B2C tariffs, promo codes, B2B exam keys):
  - ExamType                   — sellable exam kind (dtm, ...); can be switched off
  - UserSubscription           — B2C student tariff / one-time exam pass (user-scoped)
  - ExamAccessUse              — ledger of consumed access (free / weekly / key / pass)
  - PromoCode, PromoRedemption — discounts, counted only when the payment is paid
  - ExamKeyBatch, ExamKey      — B2B exam keys (tenant-scoped, RLS)
"""

import secrets
import string
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from core.managers import GlobalManager, TenantManager
from core.mixins import AuditUserMixin, RLSRowMixin, SoftDeleteMixin, TenantTimestampMixin

# ── 1. Wallet + WalletTransaction ─────────────────────────────────────────────


class Wallet(models.Model):
    """Per-user Sertifikat Coin balance. SELECT FOR UPDATE bilan atomic ops.

    Coin = integer. Top-up: 10000 UZS → 100 Coin (1 Coin = 100 UZS, default rate).

    Tenant scope (ISSUE-110 W5): user-scoped, **org-scoped emas** — B2C foydalanuvchilar
    ham wallet ishlatadi. `User.delete()` paytida CASCADE qiladi (financial obligation
    foydalanuvchi bilan birga ketadi). Cross-tenant leak yo'q — `wallet.user` FK orqali
    foydalanuvchi o'zining mablag'igagina ko'rinadi. GDPR erasure paytida
    `apps/accounts/gdpr.py` `Wallet.objects.filter(user=user)` orqali tozalanadi.
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

    # ISSUE-401: SOC2 audit trail (financial — 7 yil retention)
    history = HistoricalRecords()

    class Meta:
        verbose_name = _('Wallet')
        verbose_name_plural = _('Wallets')

    def __str__(self):
        return f'{self.user} wallet ({self.balance_coins} Coin)'


class WalletTransaction(SoftDeleteMixin, AuditUserMixin, models.Model):
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


# ── 2b. ExamType ─────────────────────────────────────────────────────────────


class ExamType(models.Model):
    """Sellable exam kind. `MockExam.exam_type` stores the `code` as a plain slug.

    Global (platform-level). `is_enabled=False` hides the type from the catalog and
    makes it non-purchasable / non-startable.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    is_enabled = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Exam Type')
        verbose_name_plural = _('Exam Types')
        ordering = ['sort_order', 'code']

    def __str__(self):
        return f'{self.name} ({self.code})'


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
        ONE_TIME = 'one_time', _('Bir martalik')

    class Audience(models.TextChoices):
        ORGANIZATION = 'organization', _('Tashkilot (B2B)')
        STUDENT = 'student', _('Talaba (B2C)')

    class AccessMode(models.TextChoices):
        # Student (B2C) products only; blank for organization plans.
        EXAM_KEY = 'exam_key', _('Bir martalik imtihon kaliti (1 urinish)')
        STUDENT = 'student', _('Talaba: tanlangan turlar cheksiz, qolganlari haftalik limit')
        ALL_TYPES = 'all_types', _('Barcha turlar cheksiz')
        SINGLE_TYPE = 'single_type', _('Bitta imtihon turi cheksiz')

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

    # B2C extension. Existing rows are organization plans (default) and unaffected.
    audience = models.CharField(
        max_length=12, choices=Audience.choices, default=Audience.ORGANIZATION, db_index=True
    )
    access_mode = models.CharField(max_length=12, choices=AccessMode.choices, blank=True)
    exam_type = models.ForeignKey(
        'ExamType',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='plans',
        help_text=_('single_type / exam_key rejimida: shu imtihon turi'),
    )
    unlimited_exam_types = models.ManyToManyField(
        'ExamType',
        blank=True,
        related_name='+',
        help_text=_('student rejimida cheksiz turlar (masalan DTM)'),
    )
    weekly_limit = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("student rejimida: cheksiz bo'lmagan turlar uchun haftalik urinishlar"),
    )
    attempts_included = models.PositiveSmallIntegerField(
        default=1, help_text=_('exam_key rejimida: nechta urinish beradi')
    )

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

    # ISSUE-401: SOC2 audit trail (price changes — financial reconciliation)
    history = HistoricalRecords()

    class Meta:
        verbose_name = _('Subscription Plan')
        verbose_name_plural = _('Subscription Plans')
        ordering = ['sort_order', 'price_uzs']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(audience='organization') | ~models.Q(access_mode=''),
                name='plan_student_requires_access_mode',
            ),
        ]

    def __str__(self):
        return f'{self.name} ({self.billing_period}, {self.pricing_model})'

    def clean(self):
        super().clean()
        if self.audience == self.Audience.ORGANIZATION:
            if self.access_mode:
                raise ValidationError({'access_mode': "Tashkilot rejasida access_mode bo'lmaydi."})
            return
        mode = self.access_mode
        if not mode:
            raise ValidationError({'access_mode': 'Talaba rejasi uchun access_mode shart.'})
        if self.price_uzs is not None and self.price_uzs < 0:
            raise ValidationError({'price_uzs': "Narx manfiy bo'lmaydi."})
        if (
            mode in (self.AccessMode.SINGLE_TYPE, self.AccessMode.EXAM_KEY)
            and not self.exam_type_id
        ):
            raise ValidationError({'exam_type': f'{mode} rejimida exam_type shart.'})
        if mode in (self.AccessMode.STUDENT, self.AccessMode.ALL_TYPES) and self.exam_type_id:
            raise ValidationError({'exam_type': f"{mode} rejimida exam_type bo'lmaydi."})
        expected = (
            self.BillingPeriod.ONE_TIME
            if mode == self.AccessMode.EXAM_KEY
            else self.BillingPeriod.MONTHLY
        )
        if self.billing_period != expected:
            raise ValidationError({'billing_period': f"{mode} uchun {expected} bo'lishi kerak."})
        if self.pricing_model != self.PricingModel.FLAT:
            raise ValidationError({'pricing_model': "Talaba rejasi faqat flat bo'ladi."})

    def calculate_charge(self, *, active_user_count: int = 0) -> Decimal:
        """Plan turidan kelib chiqib joriy charge'ni hisoblash."""
        if self.pricing_model == self.PricingModel.PER_SEAT:
            return self.price_uzs * Decimal(active_user_count)
        return self.price_uzs


class OrganizationSubscription(RLSRowMixin, AuditUserMixin, models.Model):
    """Org'ning aktiv subscription'i.

    Tenant scope (ISSUE-110 W1): `organization` FK org-scoped, lekin tarixiy sabablar
    bilan `TenantTimestampMixin` ishlatilmaydi (mavjud `id`/`organization`/`created_at`
    explicit ifoda etilgan). Defense-in-depth uchun explicit `TenantManager` + `GlobalManager`
    qo'shilgan — `.objects` org context'siz `qs.none()` qaytaradi (fail-closed).
    L3 PostgreSQL RLS ham yoqilgan (`commerce/migrations/0003_enable_rls.py`).

    Callers tenant context'siz (Celery beat task'lari, webhook view'lar) `unscoped_context()`
    bilan o'rab olishi kerak — qarang `commerce/tasks.py:_auto_renew_subscriptions_impl`.
    """

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

    # ISSUE-401: SOC2 audit trail (subscription lifecycle — 7 yil retention)
    history = HistoricalRecords()

    # ISSUE-110 W1: defense-in-depth manager — TenantManager fail-closed.
    objects = TenantManager()
    global_objects = GlobalManager()

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


# ── 5. B2C access: UserSubscription + usage ledger ───────────────────────────


class UserSubscription(models.Model):
    """B2C tariff owned by one user (created only after the payment is paid).

    Tenant scope: user-scoped like `Wallet` (B2C users may have no organization),
    so no organization FK and no RLS. One row per purchased period; buying the same
    plan again while it is current extends the existing row.
    `attempts_remaining` is used by `exam_key` plans (one-time pass), NULL otherwise.
    """

    class Status(models.TextChoices):
        ACTIVE = 'active', _('Faol')
        CANCELLED = 'cancelled', _('Bekor qilingan')
        EXPIRED = 'expired', _('Muddati tugagan')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='user_subscriptions'
    )
    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.PROTECT, related_name='user_subscriptions'
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    started_at = models.DateTimeField()
    # NULL for exam_key passes (no time limit, limited by attempts_remaining)
    current_period_ends_at = models.DateTimeField(null=True, blank=True, db_index=True)
    attempts_remaining = models.PositiveSmallIntegerField(null=True, blank=True)
    last_payment_intent = models.ForeignKey(
        PaymentIntent, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('User Subscription')
        verbose_name_plural = _('User Subscriptions')
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'status'])]

    def __str__(self):
        return f'{self.user} → {self.plan.name} ({self.status})'


class ExamAccessUse(models.Model):
    """Ledger: one row per exam start that consumed (or used) some access source.

    Free attempts used = rows with source=free; weekly usage = rows with
    source=subscription_weekly since Monday 00:00 (Asia/Tashkent).
    """

    class Source(models.TextChoices):
        FREE = 'free', _('Bepul urinish')
        SUBSCRIPTION_UNLIMITED = 'subscription_unlimited', _('Obuna (cheksiz)')
        SUBSCRIPTION_WEEKLY = 'subscription_weekly', _('Obuna (haftalik limit)')
        ORG_KEY = 'org_key', _('Tashkilot kaliti')
        EXAM_PASS = 'exam_pass', _('Bir martalik kalit')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='exam_access_uses'
    )
    exam_type = models.ForeignKey(ExamType, on_delete=models.PROTECT, related_name='+')
    source = models.CharField(max_length=24, choices=Source.choices)
    subscription = models.ForeignKey(
        UserSubscription, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    exam_key = models.ForeignKey(
        'ExamKey', on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    reference = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _('Exam Access Use')
        verbose_name_plural = _('Exam Access Uses')
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'source', '-created_at'], name='eau_user_src_idx')]

    def __str__(self):
        return f'{self.user} {self.source} {self.exam_type_id}'


# ── 6. Promo codes ───────────────────────────────────────────────────────────


class PromoCode(models.Model):
    """Case-insensitive unique code. Redemption is recorded only when paid."""

    class DiscountType(models.TextChoices):
        PERCENT = 'percent', _('Foiz')
        FIXED = 'fixed', _("Qat'iy summa (so'm)")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=32)
    discount_type = models.CharField(max_length=8, choices=DiscountType.choices)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    max_uses = models.PositiveIntegerField(null=True, blank=True, help_text=_('Cheksiz uchun null'))
    starts_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    allowed_plans = models.ManyToManyField(
        SubscriptionPlan,
        blank=True,
        related_name='promo_codes',
        help_text=_("Bo'sh bo'lsa — barcha rejalar"),
    )
    allowed_exam_types = models.ManyToManyField(
        ExamType,
        blank=True,
        related_name='promo_codes',
        help_text=_("Bo'sh bo'lsa — barcha turlar"),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Promo Code')
        verbose_name_plural = _('Promo Codes')
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(Upper('code'), name='promo_code_ci_unique'),
            models.CheckConstraint(
                condition=(
                    models.Q(discount_type='percent', value__gt=0, value__lte=100)
                    | models.Q(discount_type='fixed', value__gt=0)
                ),
                name='promo_value_valid',
            ),
        ]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = (self.code or '').strip().upper()
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        self.code = (self.code or '').strip().upper()
        if self.value is not None:
            if self.value <= 0:
                raise ValidationError({'value': "Chegirma 0 dan katta bo'lishi kerak."})
            if self.discount_type == self.DiscountType.PERCENT and self.value > 100:
                raise ValidationError({'value': 'Foiz 100 dan oshmasligi kerak.'})
        if self.starts_at and self.expires_at and self.expires_at <= self.starts_at:
            raise ValidationError({'expires_at': "expires_at starts_at dan keyin bo'lishi kerak."})


class PromoRedemption(models.Model):
    """A paid use of a promo code. (promo, user) unique = one use per user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    promo = models.ForeignKey(PromoCode, on_delete=models.PROTECT, related_name='redemptions')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='promo_redemptions'
    )
    payment_intent = models.OneToOneField(
        PaymentIntent, on_delete=models.PROTECT, related_name='promo_redemption'
    )
    discount_uzs = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Promo Redemption')
        verbose_name_plural = _('Promo Redemptions')
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['promo', 'user'], name='promo_one_use_per_user')
        ]

    def __str__(self):
        return f'{self.promo} ← {self.user}'


# ── 7. B2B exam keys (tenant-scoped, RLS in migration 0008) ──────────────────

# unambiguous characters: no 0/O/1/I
_KEY_ALPHABET = string.ascii_uppercase.translate({ord(c): None for c in 'IO'}) + '23456789'


def gen_exam_key_code() -> str:
    return ''.join(secrets.choice(_KEY_ALPHABET) for _ in range(12))


class ExamKeyBatch(TenantTimestampMixin):
    """A purchase or platform grant of N keys of one exam type to an organization."""

    class Source(models.TextChoices):
        PURCHASE = 'purchase', _('Sotib olingan')
        PLATFORM_GRANT = 'platform_grant', _('Platforma bergan')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exam_type = models.ForeignKey(ExamType, on_delete=models.PROTECT, related_name='+')
    quantity = models.PositiveIntegerField()
    source = models.CharField(max_length=16, choices=Source.choices)
    payment_intent = models.OneToOneField(
        PaymentIntent, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = _('Exam Key Batch')
        verbose_name_plural = _('Exam Key Batches')
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name='keybatch_qty_positive')
        ]

    def __str__(self):
        return f'{self.organization_id} {self.exam_type_id} x{self.quantity}'


class ExamKey(TenantTimestampMixin):
    """One key = one exam attempt of `exam_type`.

    available -> assigned (to a member) -> consumed (exam started); available/assigned
    -> revoked. Consumption is a conditional UPDATE, so a key can never be used twice.
    """

    class Status(models.TextChoices):
        AVAILABLE = 'available', _('Taqsimlanmagan')
        ASSIGNED = 'assigned', _('Taqsimlangan')
        CONSUMED = 'consumed', _('Ishlatilgan')
        REVOKED = 'revoked', _('Bekor qilingan')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(ExamKeyBatch, on_delete=models.CASCADE, related_name='keys')
    exam_type = models.ForeignKey(ExamType, on_delete=models.PROTECT, related_name='+')
    code = models.CharField(max_length=16, unique=True, default=gen_exam_key_code)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.AVAILABLE, db_index=True
    )
    group_label = models.CharField(max_length=64, blank=True, db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    assigned_at = models.DateTimeField(null=True, blank=True)
    consumed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    consumed_at = models.DateTimeField(null=True, blank=True)
    consumed_reference = models.CharField(max_length=64, blank=True)

    class Meta:
        verbose_name = _('Exam Key')
        verbose_name_plural = _('Exam Keys')
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(
                fields=['organization', 'exam_type', 'status'], name='examkey_org_type_st'
            ),
            models.Index(fields=['assigned_to', 'status'], name='examkey_assignee_st'),
        ]

    def __str__(self):
        return f'{self.code} ({self.status})'

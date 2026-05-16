"""
Wallet service — atomic operations bilan SELECT FOR UPDATE.

Bayonot: bir vaqtda ikki marta yechilmasligi uchun har spend/topup operatsiyasi
transaction.atomic() + select_for_update() ichida.

API:
  - get_or_create_wallet(user) → Wallet
  - top_up(wallet, coins, *, payment_intent=None, description='', kind=TOPUP)
  - spend(wallet, coins, *, description='', metadata=None)  → InsufficientFunds raise
  - refund(wallet, coins, *, original_tx, description='')
  - add_cash_uzs(wallet, amount_uzs, *, description='')  → affiliate cash
  - withdraw_cash_uzs(wallet, amount_uzs, *, description='')

Har operatsiyadan keyin WalletTransaction yoziladi (audit trail).
"""

from decimal import Decimal

from django.db import transaction

from .models import Wallet, WalletTransaction


class InsufficientFunds(Exception):
    """Wallet'da yetarli Coin yoki UZS yo'q."""


def get_or_create_wallet(user) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


def _record_transaction(
    wallet: Wallet,
    *,
    kind: str,
    coins_delta: int = 0,
    cash_uzs_delta: Decimal = Decimal('0.00'),
    description: str = '',
    metadata: dict | None = None,
    payment_intent=None,
) -> WalletTransaction:
    """Audit trail entry. Wallet allaqachon updated bo'lishi kerak."""
    return WalletTransaction.objects.create(
        wallet=wallet,
        kind=kind,
        coins_delta=coins_delta,
        cash_uzs_delta=cash_uzs_delta,
        balance_after_coins=wallet.balance_coins,
        balance_after_cash_uzs=wallet.pending_cash_uzs,
        description=description,
        metadata=metadata or {},
        payment_intent=payment_intent,
    )


@transaction.atomic
def top_up(
    wallet: Wallet,
    coins: int,
    *,
    payment_intent=None,
    description: str = '',
    kind: str = WalletTransaction.Kind.TOPUP,
) -> WalletTransaction:
    """Coin qo'shish. SELECT FOR UPDATE bilan atomic."""
    if coins <= 0:
        raise ValueError('coins must be positive')

    locked = Wallet.objects.select_for_update().get(pk=wallet.pk)
    locked.balance_coins += coins
    locked.save(update_fields=['balance_coins', 'updated_at'])
    return _record_transaction(
        locked,
        kind=kind,
        coins_delta=coins,
        description=description,
        payment_intent=payment_intent,
    )


@transaction.atomic
def spend(
    wallet: Wallet,
    coins: int,
    *,
    description: str = '',
    metadata: dict | None = None,
) -> WalletTransaction:
    """
    Coin yechish. Yetarli emas → InsufficientFunds raise.
    SELECT FOR UPDATE bilan double-spend himoyasi.
    """
    if coins <= 0:
        raise ValueError('coins must be positive')

    locked = Wallet.objects.select_for_update().get(pk=wallet.pk)
    if locked.balance_coins < coins:
        raise InsufficientFunds(f"Wallet'da {coins} Coin yo'q (joriy: {locked.balance_coins})")
    locked.balance_coins -= coins
    locked.save(update_fields=['balance_coins', 'updated_at'])
    return _record_transaction(
        locked,
        kind=WalletTransaction.Kind.SPEND,
        coins_delta=-coins,
        description=description,
        metadata=metadata,
    )


@transaction.atomic
def refund(
    wallet: Wallet,
    coins: int,
    *,
    original_tx: WalletTransaction | None = None,
    description: str = '',
) -> WalletTransaction:
    """SPEND'ni qaytarish (admin yoki avtomat refund flow)."""
    if coins <= 0:
        raise ValueError('coins must be positive')

    locked = Wallet.objects.select_for_update().get(pk=wallet.pk)
    locked.balance_coins += coins
    locked.save(update_fields=['balance_coins', 'updated_at'])

    metadata = {}
    if original_tx is not None:
        metadata['original_tx_id'] = str(original_tx.id)
    return _record_transaction(
        locked,
        kind=WalletTransaction.Kind.REFUND,
        coins_delta=coins,
        description=description,
        metadata=metadata,
    )


@transaction.atomic
def add_cash_uzs(
    wallet: Wallet,
    amount_uzs: Decimal,
    *,
    description: str = '',
    kind: str = WalletTransaction.Kind.AFFILIATE_PAYOUT,
) -> WalletTransaction:
    """Affiliate'dan kelgan naqd UZS qo'shish."""
    if amount_uzs <= 0:
        raise ValueError('amount_uzs must be positive')
    locked = Wallet.objects.select_for_update().get(pk=wallet.pk)
    locked.pending_cash_uzs += amount_uzs
    locked.save(update_fields=['pending_cash_uzs', 'updated_at'])
    return _record_transaction(
        locked,
        kind=kind,
        cash_uzs_delta=amount_uzs,
        description=description,
    )


@transaction.atomic
def withdraw_cash_uzs(
    wallet: Wallet,
    amount_uzs: Decimal,
    *,
    description: str = '',
) -> WalletTransaction:
    """Naqd UZS yechish (foydalanuvchi bank kartasiga)."""
    if amount_uzs <= 0:
        raise ValueError('amount_uzs must be positive')
    locked = Wallet.objects.select_for_update().get(pk=wallet.pk)
    if locked.pending_cash_uzs < amount_uzs:
        raise InsufficientFunds(f'Naqd UZS yetarli emas (joriy: {locked.pending_cash_uzs})')
    locked.pending_cash_uzs -= amount_uzs
    locked.save(update_fields=['pending_cash_uzs', 'updated_at'])
    return _record_transaction(
        locked,
        kind=WalletTransaction.Kind.AFFILIATE_PAYOUT,
        cash_uzs_delta=-amount_uzs,
        description=description,
    )

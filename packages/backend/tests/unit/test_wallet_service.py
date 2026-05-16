"""
Unit tests for apps/commerce/wallet_service.py

Focus: atomic Coin/UZS operations, SELECT FOR UPDATE behavior, audit trail,
insufficient funds guards. DB is real; Redis mocked (autouse).
"""

from decimal import Decimal

import pytest

from apps.commerce import wallet_service
from apps.commerce.models import PaymentIntent, Wallet, WalletTransaction


@pytest.mark.unit
class TestGetOrCreateWallet:
    """get_or_create_wallet — idempotent factory."""

    def test_creates_wallet_for_user(self, db, user):
        """First call yangi Wallet yaratadi."""
        wallet = wallet_service.get_or_create_wallet(user)
        assert wallet.user_id == user.id
        assert wallet.balance_coins == 0
        assert wallet.pending_cash_uzs == Decimal('0.00')

    def test_idempotent_second_call(self, db, user):
        """Ikkinchi marta chaqirilsa, mavjud Wallet qaytariladi."""
        w1 = wallet_service.get_or_create_wallet(user)
        w2 = wallet_service.get_or_create_wallet(user)
        assert w1.pk == w2.pk
        assert Wallet.objects.filter(user=user).count() == 1

    def test_separate_wallets_per_user(self, db, user, user2):
        """Har user uchun alohida wallet bo'ladi."""
        w1 = wallet_service.get_or_create_wallet(user)
        w2 = wallet_service.get_or_create_wallet(user2)
        assert w1.pk != w2.pk


@pytest.mark.unit
class TestTopUp:
    """top_up — Coin qo'shish + audit trail."""

    @pytest.fixture
    def wallet(self, db, user):
        return wallet_service.get_or_create_wallet(user)

    def test_top_up_increases_balance(self, wallet):
        """100 Coin top-up → balance_coins = 100."""
        wallet_service.top_up(wallet, 100, description='Test topup')
        wallet.refresh_from_db()
        assert wallet.balance_coins == 100

    def test_top_up_records_transaction(self, wallet):
        """Har top-up audit trail qoldiradi."""
        tx = wallet_service.top_up(wallet, 50, description='Welcome bonus')
        assert tx.kind == WalletTransaction.Kind.TOPUP
        assert tx.coins_delta == 50
        assert tx.balance_after_coins == 50
        assert tx.description == 'Welcome bonus'

    def test_top_up_zero_raises(self, wallet):
        """Coins must be positive."""
        with pytest.raises(ValueError, match='positive'):
            wallet_service.top_up(wallet, 0)

    def test_top_up_negative_raises(self, wallet):
        """Negative coins → ValueError."""
        with pytest.raises(ValueError, match='positive'):
            wallet_service.top_up(wallet, -10)

    def test_top_up_with_payment_intent(self, db, user, wallet):
        """payment_intent FK yoziladi tx'ga."""
        intent = PaymentIntent.objects.create(
            user=user,
            provider=PaymentIntent.Provider.STUB,
            amount_uzs=Decimal('10000'),
            coins_to_credit=100,
        )
        tx = wallet_service.top_up(wallet, 100, payment_intent=intent)
        assert tx.payment_intent_id == intent.id

    def test_top_up_custom_kind(self, wallet):
        """Custom kind (e.g. REFERRAL_BONUS) qabul qilinadi."""
        tx = wallet_service.top_up(wallet, 25, kind=WalletTransaction.Kind.REFERRAL_BONUS)
        assert tx.kind == WalletTransaction.Kind.REFERRAL_BONUS

    def test_multiple_top_ups_accumulate(self, wallet):
        """Bir necha top-up balansga jamlanadi."""
        wallet_service.top_up(wallet, 100)
        wallet_service.top_up(wallet, 50)
        wallet_service.top_up(wallet, 25)
        wallet.refresh_from_db()
        assert wallet.balance_coins == 175


@pytest.mark.unit
class TestSpend:
    """spend — Coin yechish + InsufficientFunds guard."""

    @pytest.fixture
    def funded_wallet(self, db, user):
        w = wallet_service.get_or_create_wallet(user)
        wallet_service.top_up(w, 500)
        w.refresh_from_db()
        return w

    def test_spend_decreases_balance(self, funded_wallet):
        """Spend qilingach, balans kamayadi."""
        wallet_service.spend(funded_wallet, 100, description='Premium feature')
        funded_wallet.refresh_from_db()
        assert funded_wallet.balance_coins == 400

    def test_spend_records_negative_delta(self, funded_wallet):
        """SPEND tx coins_delta negative bo'ladi."""
        tx = wallet_service.spend(funded_wallet, 200)
        assert tx.kind == WalletTransaction.Kind.SPEND
        assert tx.coins_delta == -200
        assert tx.balance_after_coins == 300

    def test_spend_insufficient_funds(self, funded_wallet):
        """Balans yetmasa InsufficientFunds raise bo'ladi."""
        with pytest.raises(wallet_service.InsufficientFunds):
            wallet_service.spend(funded_wallet, 1000)

    def test_spend_insufficient_does_not_change_balance(self, funded_wallet):
        """Failed spend balansga ta'sir qilmaydi."""
        try:
            wallet_service.spend(funded_wallet, 1000)
        except wallet_service.InsufficientFunds:
            pass
        funded_wallet.refresh_from_db()
        assert funded_wallet.balance_coins == 500

    def test_spend_zero_raises(self, funded_wallet):
        with pytest.raises(ValueError, match='positive'):
            wallet_service.spend(funded_wallet, 0)

    def test_spend_with_metadata(self, funded_wallet):
        """Metadata JSON tx'da saqlanadi."""
        tx = wallet_service.spend(
            funded_wallet, 50, metadata={'feature': 'ai_diagnostic', 'session_id': 'abc'}
        )
        assert tx.metadata['feature'] == 'ai_diagnostic'
        assert tx.metadata['session_id'] == 'abc'

    def test_spend_exactly_balance(self, funded_wallet):
        """Aynan balansga teng spend ishlaydi (= emas, < check)."""
        wallet_service.spend(funded_wallet, 500)
        funded_wallet.refresh_from_db()
        assert funded_wallet.balance_coins == 0


@pytest.mark.unit
class TestRefund:
    """refund — SPEND'ni qaytarish."""

    @pytest.fixture
    def wallet_with_spend(self, db, user):
        w = wallet_service.get_or_create_wallet(user)
        wallet_service.top_up(w, 500)
        spend_tx = wallet_service.spend(w, 200)
        w.refresh_from_db()
        return w, spend_tx

    def test_refund_increases_balance(self, wallet_with_spend):
        """Refund qilingach, balans qaytadi."""
        wallet, _ = wallet_with_spend
        wallet_service.refund(wallet, 200)
        wallet.refresh_from_db()
        assert wallet.balance_coins == 500

    def test_refund_records_original_tx_id(self, wallet_with_spend):
        """original_tx metadata'ga yoziladi."""
        wallet, spend_tx = wallet_with_spend
        tx = wallet_service.refund(wallet, 200, original_tx=spend_tx)
        assert tx.kind == WalletTransaction.Kind.REFUND
        assert tx.metadata['original_tx_id'] == str(spend_tx.id)

    def test_refund_without_original_tx(self, wallet_with_spend):
        """original_tx ixtiyoriy."""
        wallet, _ = wallet_with_spend
        tx = wallet_service.refund(wallet, 100)
        assert tx.metadata == {}

    def test_refund_zero_raises(self, wallet_with_spend):
        wallet, _ = wallet_with_spend
        with pytest.raises(ValueError, match='positive'):
            wallet_service.refund(wallet, 0)


@pytest.mark.unit
class TestCashUZS:
    """add_cash_uzs / withdraw_cash_uzs — naqd UZS operatsiyalari."""

    @pytest.fixture
    def wallet(self, db, user):
        return wallet_service.get_or_create_wallet(user)

    def test_add_cash_uzs(self, wallet):
        """Naqd UZS qo'shish — pending_cash_uzs oshadi."""
        wallet_service.add_cash_uzs(wallet, Decimal('50000.00'))
        wallet.refresh_from_db()
        assert wallet.pending_cash_uzs == Decimal('50000.00')

    def test_add_cash_uzs_records_tx(self, wallet):
        """Cash add audit trail qoldiradi."""
        tx = wallet_service.add_cash_uzs(wallet, Decimal('25000.00'))
        assert tx.kind == WalletTransaction.Kind.AFFILIATE_PAYOUT
        assert tx.cash_uzs_delta == Decimal('25000.00')
        assert tx.balance_after_cash_uzs == Decimal('25000.00')

    def test_add_cash_uzs_zero_raises(self, wallet):
        with pytest.raises(ValueError, match='positive'):
            wallet_service.add_cash_uzs(wallet, Decimal('0'))

    def test_withdraw_cash_uzs_decreases_pending(self, wallet):
        """Withdraw → pending_cash_uzs kamayadi."""
        wallet_service.add_cash_uzs(wallet, Decimal('100000.00'))
        wallet_service.withdraw_cash_uzs(wallet, Decimal('30000.00'))
        wallet.refresh_from_db()
        assert wallet.pending_cash_uzs == Decimal('70000.00')

    def test_withdraw_cash_uzs_insufficient(self, wallet):
        """Naqd yetarli emas → InsufficientFunds."""
        wallet_service.add_cash_uzs(wallet, Decimal('10000.00'))
        with pytest.raises(wallet_service.InsufficientFunds):
            wallet_service.withdraw_cash_uzs(wallet, Decimal('50000.00'))

    def test_withdraw_cash_uzs_zero_raises(self, wallet):
        with pytest.raises(ValueError, match='positive'):
            wallet_service.withdraw_cash_uzs(wallet, Decimal('0'))

    def test_withdraw_records_negative_delta(self, wallet):
        """Withdraw tx'da cash_uzs_delta negative."""
        wallet_service.add_cash_uzs(wallet, Decimal('100000.00'))
        tx = wallet_service.withdraw_cash_uzs(wallet, Decimal('40000.00'))
        assert tx.cash_uzs_delta == Decimal('-40000.00')
        assert tx.balance_after_cash_uzs == Decimal('60000.00')

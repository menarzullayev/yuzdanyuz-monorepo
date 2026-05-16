"""
Unit tests for apps/commerce/affiliate_service.py

Focus: referral code creation, claim flow, Coin bonus, threshold-based
cash withdrawal unlock, idempotency guards. DB is real; Redis mocked.
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.commerce import affiliate_service, wallet_service
from apps.commerce.models import Referral, ReferralCode, WalletTransaction

User = get_user_model()


@pytest.mark.unit
class TestGetOrCreateCode:
    """get_or_create_code — idempotent ReferralCode factory."""

    def test_creates_code_for_user(self, db, user):
        """Birinchi chaqiruv code yaratadi."""
        code = affiliate_service.get_or_create_code(user)
        assert code.user_id == user.id
        assert len(code.code) == 8
        assert code.is_withdrawable is False

    def test_idempotent(self, db, user):
        """Ikkinchi marta chaqirilsa, eski code qaytariladi."""
        c1 = affiliate_service.get_or_create_code(user)
        c2 = affiliate_service.get_or_create_code(user)
        assert c1.pk == c2.pk
        assert ReferralCode.objects.filter(user=user).count() == 1

    def test_separate_codes_per_user(self, db, user, user2):
        """Har user uchun alohida code."""
        c1 = affiliate_service.get_or_create_code(user)
        c2 = affiliate_service.get_or_create_code(user2)
        assert c1.code != c2.code


@pytest.mark.unit
class TestClaimReferral:
    """claim_referral — yangi signup uchun referral kredit."""

    def test_claim_creates_referral_record(self, db, user, user2):
        """Valid code → Referral yaratiladi."""
        code = affiliate_service.get_or_create_code(user)
        referral = affiliate_service.claim_referral(code.code, user2)
        assert referral is not None
        assert referral.inviter_id == user.id
        assert referral.invited_id == user2.id

    def test_claim_credits_inviter_wallet(self, db, user, user2):
        """Inviter wallet'iga Coin bonus tushadi (default 50)."""
        code = affiliate_service.get_or_create_code(user)
        affiliate_service.claim_referral(code.code, user2)

        wallet = wallet_service.get_or_create_wallet(user)
        wallet.refresh_from_db()
        assert wallet.balance_coins == 50

    def test_claim_records_referral_bonus_tx(self, db, user, user2):
        """REFERRAL_BONUS WalletTransaction yoziladi."""
        code = affiliate_service.get_or_create_code(user)
        affiliate_service.claim_referral(code.code, user2)

        wallet = wallet_service.get_or_create_wallet(user)
        tx = WalletTransaction.objects.filter(
            wallet=wallet, kind=WalletTransaction.Kind.REFERRAL_BONUS
        ).first()
        assert tx is not None
        assert tx.coins_delta == 50

    def test_claim_invalid_code_returns_none(self, db, user2):
        """Mavjud bo'lmagan code → None (no error)."""
        result = affiliate_service.claim_referral('INVALID1', user2)
        assert result is None

    def test_claim_empty_code_returns_none(self, db, user2):
        """Bo'sh code → None."""
        assert affiliate_service.claim_referral('', user2) is None
        assert affiliate_service.claim_referral(None, user2) is None

    def test_claim_self_referral_ignored(self, db, user):
        """User o'z code'ini ishlatsa → None."""
        code = affiliate_service.get_or_create_code(user)
        result = affiliate_service.claim_referral(code.code, user)
        assert result is None
        assert Referral.objects.filter(invited=user).count() == 0

    def test_claim_idempotent_per_invited(self, db, user, user2):
        """Bir invited user ikki marta claim qila olmaydi."""
        code = affiliate_service.get_or_create_code(user)
        first = affiliate_service.claim_referral(code.code, user2)
        second = affiliate_service.claim_referral(code.code, user2)
        assert first is not None
        assert second is None
        assert Referral.objects.filter(invited=user2).count() == 1

    def test_claim_code_normalized_to_upper(self, db, user, user2):
        """Code lowercase'da kelsa ham topiladi."""
        code = affiliate_service.get_or_create_code(user)
        result = affiliate_service.claim_referral(code.code.lower(), user2)
        assert result is not None

    def test_claim_code_stripped(self, db, user, user2):
        """Code atrofidagi bo'sh joylar olib tashlanadi."""
        code = affiliate_service.get_or_create_code(user)
        result = affiliate_service.claim_referral(f'  {code.code}  ', user2)
        assert result is not None

    def test_claim_does_not_unlock_withdrawal_below_threshold(self, db, user, user2):
        """Threshold (50) dan kam referral → is_withdrawable=False."""
        code = affiliate_service.get_or_create_code(user)
        affiliate_service.claim_referral(code.code, user2)
        code.refresh_from_db()
        assert code.is_withdrawable is False

    def test_claim_unlocks_withdrawal_at_threshold(self, db, user, settings):
        """Threshold yetganda is_withdrawable=True + naqd UZS qo'shiladi."""
        # Test'ni tezlash uchun threshold'ni 3'ga tushiramiz
        settings.REFERRAL_WITHDRAWAL_THRESHOLD = 3
        settings.REFERRAL_COIN_BONUS = 50
        settings.REFERRAL_PAYOUT_PER_USER_UZS = 5000

        code = affiliate_service.get_or_create_code(user)

        # 3 ta invited user yaratamiz
        for i in range(3):
            invited = User.objects.create_user(
                username=f'invited{i}', email=f'inv{i}@e.com', password='p'
            )
            affiliate_service.claim_referral(code.code, invited)

        code.refresh_from_db()
        assert code.is_withdrawable is True

        # Naqd UZS qo'shilganini tekshiramiz: 3 referrals * 5000 = 15000
        wallet = wallet_service.get_or_create_wallet(user)
        wallet.refresh_from_db()
        assert wallet.pending_cash_uzs == Decimal('15000')

    def test_claim_cash_bonus_only_once(self, db, user, settings):
        """Threshold yetilgan bo'lsa, keyingi referrallarda cash qayta qo'shilmaydi."""
        settings.REFERRAL_WITHDRAWAL_THRESHOLD = 2
        settings.REFERRAL_COIN_BONUS = 50
        settings.REFERRAL_PAYOUT_PER_USER_UZS = 5000

        code = affiliate_service.get_or_create_code(user)

        # 2 ta — threshold yetadi (2 * 5000 = 10000 UZS)
        for i in range(2):
            invited = User.objects.create_user(username=f'u{i}', email=f'u{i}@e.com', password='p')
            affiliate_service.claim_referral(code.code, invited)

        wallet = wallet_service.get_or_create_wallet(user)
        wallet.refresh_from_db()
        first_cash = wallet.pending_cash_uzs
        assert first_cash == Decimal('10000')

        # 3-chi referral — cash QO'SHILMAYDI (allaqachon withdrawable)
        extra = User.objects.create_user(username='u3', email='u3@e.com', password='p')
        affiliate_service.claim_referral(code.code, extra)

        wallet.refresh_from_db()
        assert wallet.pending_cash_uzs == first_cash

    def test_multiple_invites_accumulate_coins(self, db, user, settings):
        """Har invite Coin qo'shadi (50 * N)."""
        settings.REFERRAL_COIN_BONUS = 50
        settings.REFERRAL_WITHDRAWAL_THRESHOLD = 999  # threshold yetmasin

        code = affiliate_service.get_or_create_code(user)
        for i in range(4):
            invited = User.objects.create_user(username=f'a{i}', email=f'a{i}@e.com', password='p')
            affiliate_service.claim_referral(code.code, invited)

        wallet = wallet_service.get_or_create_wallet(user)
        wallet.refresh_from_db()
        assert wallet.balance_coins == 200  # 4 * 50

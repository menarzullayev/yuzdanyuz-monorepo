"""
Affiliate service — referral codes + Coin bonus + cash withdrawal threshold.

Settings:
  REFERRAL_COIN_BONUS         = 50      (har signup uchun bonus)
  REFERRAL_WITHDRAWAL_THRESHOLD = 50    (50+ referrals → naqd UZS yechish)
  REFERRAL_PAYOUT_PER_USER_UZS  = 5000  (har referral uchun naqd UZS)

API:
  - get_or_create_code(user) → ReferralCode
  - claim_referral(code, new_user) → Referral (creates if not exists)
"""

from decimal import Decimal

from django.conf import settings
from django.db import transaction

from . import wallet_service
from .models import Referral, ReferralCode, WalletTransaction


def get_or_create_code(user) -> ReferralCode:
    code, _ = ReferralCode.objects.get_or_create(user=user)
    return code


@transaction.atomic
def claim_referral(code_str: str, new_user) -> Referral | None:
    """
    Yangi user signup'da chaqiriladi. Code valid bo'lsa:
      - Referral record yaratish
      - Inviter wallet'iga Coin bonus
      - Inviter referral count threshold'dan o'tsa is_withdrawable=True

    Idempotent: bir invited user faqat bir marta referral'ga ega bo'ladi.
    """
    code_str = (code_str or '').strip().upper()
    if not code_str:
        return None

    try:
        code = ReferralCode.objects.select_related('user').get(code=code_str)
    except ReferralCode.DoesNotExist:
        return None

    if code.user_id == new_user.id:
        # User o'z code'ini ishlatdi — ignore
        return None

    if Referral.objects.filter(invited=new_user).exists():
        # Allaqachon referral'ga ega
        return None

    bonus = int(getattr(settings, 'REFERRAL_COIN_BONUS', 50))
    referral = Referral.objects.create(
        inviter=code.user,
        invited=new_user,
        coin_reward=bonus,
    )

    # Coin bonus inviter wallet'iga
    inviter_wallet = wallet_service.get_or_create_wallet(code.user)
    wallet_service.top_up(
        inviter_wallet,
        bonus,
        description=f'Referral bonus: {new_user}',
        kind=WalletTransaction.Kind.REFERRAL_BONUS,
    )

    # Naqd cash threshold check
    threshold = int(getattr(settings, 'REFERRAL_WITHDRAWAL_THRESHOLD', 50))
    total_referrals = Referral.objects.filter(inviter=code.user).count()
    if total_referrals >= threshold and not code.is_withdrawable:
        code.is_withdrawable = True
        code.save(update_fields=['is_withdrawable'])

        # Bir martalik naqd UZS bonus (threshold yetganda)
        cash_per_user = Decimal(getattr(settings, 'REFERRAL_PAYOUT_PER_USER_UZS', 5000))
        total_cash = cash_per_user * total_referrals
        wallet_service.add_cash_uzs(
            inviter_wallet,
            total_cash,
            description=f'{total_referrals} referrals threshold bonus',
        )

    return referral

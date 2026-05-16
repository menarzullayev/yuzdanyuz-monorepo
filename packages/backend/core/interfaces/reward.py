"""
RewardService Protocol — `engagement → commerce` yo'nalishi uchun kontrakt.

Engagement domeni (streak/league milestones) Coin reward berishi kerak, lekin
commerce paketga to'g'ridan-to'g'ri bog'lanmasdan. Protocol + lazy resolver:
  - Test'lar uchun mock implementation berish oson (settings override yoki
    monkeypatch).
  - Circular-import muammosi yo'q (resolver runtime'da chaqiriladi).
  - Dependency direction explicit: engagement Protocol e'lon qiladi, commerce
    adapter beradi.
"""

from typing import Protocol


class RewardService(Protocol):
    """Interface for awarding Coin rewards. engagement → commerce."""

    def __call__(
        self,
        user_id,
        amount: int,
        *,
        reason: str,
        ref_id: str = '',
    ) -> None: ...


def get_reward_service() -> RewardService:
    """Lazy resolver — returns the configured reward implementation.

    Default: apps.commerce.wallet_service (top_up adapter).
    Override via settings.REWARD_SERVICE_PATH for tests/alternative providers.
    """
    from django.conf import settings
    from django.utils.module_loading import import_string

    path = getattr(
        settings,
        'REWARD_SERVICE_PATH',
        'apps.commerce.wallet_service.award_coins_adapter',
    )
    return import_string(path)

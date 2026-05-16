"""
Core interfaces — Protocol-based contracts between apps.

Yo'naltirilgan dependency'larni explicit qilish uchun: yuqori-level paket
(masalan, engagement) past-level paketga (commerce) bog'liqlikni Protocol
orqali e'lon qiladi, real implementation lazy resolver orqali topiladi.

Bu pattern circular-import xatarini va implicit `try/except ImportError`
hacklarini yo'q qiladi.
"""

from .reward import RewardService, get_reward_service

__all__ = ['RewardService', 'get_reward_service']

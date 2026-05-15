"""
Membership cache invalidation signals.

TenantMiddleware membership existence'ni Redis'da 60s TTL bilan cache qiladi.
Membership o'zgarsa (yaratilsa, status o'zgarsa, o'chirilsa) — cache key'ni
o'chirib, keyingi request DB'dan haqiqiy holatni o'qiydi.

Eslatma: 60s TTL bizning bir-tomonlama bo'shliqdir — agar signal nimadandir
o'tkazib yuborilsa, eski qiymat 60s ichida o'z-o'zidan tugaydi.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.membership_cache import invalidate

from .models import Membership


@receiver(post_save, sender=Membership)
def invalidate_on_save(sender, instance, **kwargs):
    invalidate(instance.user_id, instance.organization_id)


@receiver(post_delete, sender=Membership)
def invalidate_on_delete(sender, instance, **kwargs):
    invalidate(instance.user_id, instance.organization_id)

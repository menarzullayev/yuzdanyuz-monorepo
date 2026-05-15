"""
TenantMixin — tenant-related barcha modellarga qo'shiladigan abstract base.

Ishlatish:
    class Question(TenantMixin):
        title = models.CharField(...)
        # organization field va manager avtomatik keladi

    # Tenant context o'rnatilgan bo'lsa:
    Question.objects.all()          # faqat joriy org savollari
    Question.global_objects.all()   # barcha tenantlar (admin uchun)
"""

from django.db import models

from .managers import GlobalManager, TenantManager


class TenantMixin(models.Model):
    """
    Abstract mixin. Meros olgan har bir model:
        - organization FK oladi
        - objects = TenantManager (avtomatik filter)
        - global_objects = GlobalManager (filter bypass)
    """

    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='%(app_label)s_%(class)s_set',
        db_index=True,
        verbose_name='Tashkilot',
    )

    objects = TenantManager()
    global_objects = GlobalManager()

    class Meta:
        abstract = True


class TimestampMixin(models.Model):
    """created_at / updated_at — barcha modellarga qo'shiladi."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantTimestampMixin(TenantMixin, TimestampMixin):
    """
    Eng ko'p ishlatiladigan kombinatsiya:
        organization FK + created_at + updated_at

    Ishlatish:
        class Question(TenantTimestampMixin):
            title = models.CharField(...)
    """

    class Meta:
        abstract = True

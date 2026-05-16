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

from django.conf import settings
from django.db import models
from django.utils import timezone

from .audit_user import get_current_user
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


# ── ISSUE-103: Soft-delete audit modellariga ─────────────────────────────────


class SoftDeleteMixin(models.Model):
    """ISSUE-103: audit modellari uchun soft-delete + PII redaction.

    Maqsad: GDPR Article 17 (right to erasure) talabini financial/audit data
    saqlash bilan birga qondirish. User PII (email/phone/name) anonimlashtirildi,
    lekin audit ID'lar saqlanadi (UZ buxgalteriya 5 yil, ICDL/DTM compliance).

    Bu mixin FAQAT field'lar qo'shadi (is_deleted, deleted_at, pii_redacted).
    Default queryset filter YO'Q — TenantManager bilan konflikt yo'q, audit
    view'lar default'da hammasini ko'radi.

    Usage:
        # Application-darajadagi filter:
        WalletTransaction.objects.filter(is_deleted=False)  # tirik qatorlar
        # Yoki helper:
        WalletTransaction.alive()                            # tirik
        WalletTransaction.deleted()                          # o'chirilgan

        # Soft-delete:
        tx = WalletTransaction.objects.get(...)
        tx.soft_delete(redact_pii=True)
    """

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    pii_redacted = models.BooleanField(default=False)

    # Subclass o'rnatadi — qaysi field'lar PII redaction'ga tegishli.
    # Misol: pii_fields = ['user_email_snapshot', 'phone']
    pii_fields: tuple[str, ...] = ()

    class Meta:
        abstract = True

    @classmethod
    def alive(cls):
        """Helper: faqat tirik qatorlar."""
        return cls.objects.filter(is_deleted=False)

    @classmethod
    def deleted(cls):
        """Helper: faqat o'chirilgan qatorlar."""
        return cls.objects.filter(is_deleted=True)

    def soft_delete(self, *, redact_pii: bool = False) -> None:
        """Mark as deleted, optionally redact PII fields."""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        update_fields = ['is_deleted', 'deleted_at']
        if redact_pii and self.pii_fields:
            for field_name in self.pii_fields:
                if hasattr(self, field_name):
                    setattr(self, field_name, '')
                    update_fields.append(field_name)
            self.pii_redacted = True
            update_fields.append('pii_redacted')
        self.save(update_fields=update_fields)

    def restore(self) -> None:
        """Soft-delete'ni qaytarish (PII redaction irreversible)."""
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=['is_deleted', 'deleted_at'])


# ── ISSUE-409: created_by / updated_by audit fields ─────────────────────────


class AuditUserMixin(models.Model):
    """ISSUE-409: SOC2/audit prep — har row uchun kim yaratgan/oxirgi
    o'zgartirgan saqlanadi.

    `AuditUserMiddleware` har request boshida `set_current_user(request.user)`
    chaqiradi. Bu mixin `save()` paytida ContextVar'dan o'qib `created_by`
    (faqat insert), `updated_by` (har save) FK'ni to'ldiradi.

    Anonymous request, Celery task, management command, system fixture →
    user=None → FK NULL qoldiriladi (SOC2 "system" marker; explicit set uchun
    `with audit_user_context(admin): ...`).

    Both fields use SET_NULL: kim yaratgan'i o'chirilsa ham audit qator saqlanadi.
    """

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        editable=False,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        editable=False,
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        user = get_current_user()
        if user is not None and getattr(user, 'is_authenticated', False):
            if self._state.adding and self.created_by_id is None:
                self.created_by = user
            self.updated_by = user
            # save() bilan update_fields= berilgan bo'lsa, audit field'larini
            # ham qo'shamiz (aks holda DB'da yangilanmaydi).
            update_fields = kwargs.get('update_fields')
            if update_fields is not None:
                update_fields = set(update_fields)
                update_fields.add('updated_by')
                if self._state.adding:
                    update_fields.add('created_by')
                kwargs['update_fields'] = list(update_fields)
        super().save(*args, **kwargs)

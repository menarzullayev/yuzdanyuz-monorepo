"""
Tenant context — joriy organization'ni ContextVar orqali saqlaydi.

ContextVar tanlovi:
  - threading.local() o'rniga — chunki async view'larda noto'g'ri ishlaydi
    (bir thread ichida bir nechta corutine bir-birining org'ini ko'radi).
  - ContextVar PEP 567 standarti — sync va async ikkalasida to'g'ri.
  - Token-based reset (set/reset) — to'g'ri nesting'ni avtomat ta'minlaydi.

Ishlatish:
    from core.tenant import get_current_org, set_current_org, tenant_context

    # View yoki service ichida:
    org = get_current_org()

    # Test yoki management command ichida:
    with tenant_context(org):
        Question.objects.all()  # faqat shu org savollari

Unscoped (admin/worker uchun, tenant filter'siz):
    from core.tenant import unscoped_context
    with unscoped_context():
        Question.objects.all()  # barcha tenantlar
"""

from contextlib import contextmanager
from contextvars import ContextVar

# `Optional` annotation atayyin yozilmagan — None bo'lishi runtime'da yetarlicha aniq.
_current_org: ContextVar = ContextVar('tenant_current_org', default=None)
_unscoped_allowed: ContextVar = ContextVar('tenant_unscoped_allowed', default=False)


def get_current_org():
    """Joriy aktiv organization'ni qaytaradi. Yo'q bo'lsa None."""
    return _current_org.get()


def set_current_org(org):
    """Joriy aktiv organization'ni o'rnatadi (middleware request boshida chaqiradi)."""
    _current_org.set(org)


def clear_current_org():
    """Context'ni tozalaydi (middleware request oxirida chaqiradi)."""
    _current_org.set(None)


@contextmanager
def tenant_context(org):
    """
    Test va management command'larda tenant'ni vaqtinchalik o'rnatish.

    with tenant_context(org):
        qs = Question.objects.all()   # faqat org savollari
    # blokdan chiqqanda Token avtomatik reset qiladi (nesting xavfsiz)
    """
    token = _current_org.set(org)
    try:
        yield
    finally:
        _current_org.reset(token)


# ── Unscoped context (admin/worker bypass) ────────────────────
# Default'da TenantManager fail-closed: org=None bo'lsa empty queryset.
# Bu marker explicit ravishda "men barcha tenantlar ma'lumotini olishni xohlayman"
# deb belgilashga imkon beradi (admin, background worker, bootstrap script).


def is_unscoped_allowed() -> bool:
    return _unscoped_allowed.get()


def set_unscoped_allowed(allowed: bool):
    _unscoped_allowed.set(allowed)


@contextmanager
def unscoped_context():
    """
    Tenant filter'ni explicit bypass qilish.

    with unscoped_context():
        Question.objects.all()   # barcha tenantlar

    Ishlatish joylari:
        - Admin'da cross-tenant ko'rinish
        - Celery background worker
        - Bootstrap / data migration script
        - TenantMiddleware ichida _resolve_org (chicken-and-egg)
    """
    token = _unscoped_allowed.set(True)
    try:
        yield
    finally:
        _unscoped_allowed.reset(token)

"""
ISSUE-103 — Soft-delete + GDPR erasure tests.

Audit modellari:
  - is_deleted, deleted_at, pii_redacted field'lari mavjudligi
  - soft_delete(redact_pii=True) PII'ni '' qiladi, ID + statistikalar saqlanadi
  - GDPR erasure endpoint user PII'ni anonimlashtiradi va audit'ni saqlaydi
"""

import pytest
from django.test import Client
from django.urls import reverse

from apps.commerce import wallet_service
from apps.commerce.models import Wallet


@pytest.mark.integration
class TestSoftDeleteMixin:
    def test_walletransaction_has_soft_delete_fields(self, db, user):
        """SoftDeleteMixin field'lari WalletTransaction'ga qo'shilgan."""
        from apps.commerce.models import WalletTransaction

        w, _ = Wallet.objects.get_or_create(user=user)
        tx = wallet_service.top_up(w, 10, description='test')
        # Field default'lari
        assert tx.is_deleted is False
        assert tx.deleted_at is None
        assert tx.pii_redacted is False

    def test_soft_delete_marks_row_keeps_data(self, db, user):
        """soft_delete() row'ni o'chirmaydi, faqat mark qiladi."""
        from apps.commerce.models import WalletTransaction

        w, _ = Wallet.objects.get_or_create(user=user)
        tx = wallet_service.top_up(w, 10, description='Sample description')
        tx_id = tx.id

        tx.soft_delete()
        tx.refresh_from_db()
        assert tx.is_deleted is True
        assert tx.deleted_at is not None
        assert tx.pii_redacted is False  # redact yo'q
        assert tx.description == 'Sample description'  # PII saqlangan

        # DB'da hali ham mavjud
        assert WalletTransaction.objects.filter(id=tx_id).exists()

    def test_soft_delete_with_pii_redaction(self, db, user):
        """redact_pii=True bilan PII field'lari '' bo'ladi."""
        w, _ = Wallet.objects.get_or_create(user=user)
        tx = wallet_service.top_up(w, 10, description='Sensitive user note')
        amount_snapshot = tx.coins_delta

        tx.soft_delete(redact_pii=True)
        tx.refresh_from_db()
        assert tx.is_deleted is True
        assert tx.pii_redacted is True
        assert tx.description == ''  # PII redacted
        # Amount + status statistikalar saqlangan (audit uchun)
        assert tx.coins_delta == amount_snapshot

    def test_alive_helper_filters_deleted(self, db, user):
        """SoftDeleteMixin.alive() faqat tirik qatorlarni qaytaradi."""
        from apps.commerce.models import WalletTransaction

        w, _ = Wallet.objects.get_or_create(user=user)
        tx1 = wallet_service.top_up(w, 10, description='tx1')
        tx2 = wallet_service.top_up(w, 20, description='tx2')

        tx1.soft_delete()

        alive_ids = set(WalletTransaction.alive().values_list('id', flat=True))
        deleted_ids = set(WalletTransaction.deleted().values_list('id', flat=True))
        all_ids = set(WalletTransaction.objects.values_list('id', flat=True))

        assert tx1.id not in alive_ids
        assert tx2.id in alive_ids
        assert tx1.id in deleted_ids
        assert {tx1.id, tx2.id}.issubset(all_ids)

    def test_restore_unmarks_deletion(self, db, user):
        """restore() soft-delete'ni qaytaradi (lekin PII redaction qaytmaydi)."""
        w, _ = Wallet.objects.get_or_create(user=user)
        tx = wallet_service.top_up(w, 10, description='test')

        tx.soft_delete()
        assert tx.is_deleted is True

        tx.restore()
        tx.refresh_from_db()
        assert tx.is_deleted is False
        assert tx.deleted_at is None


@pytest.mark.integration
class TestGDPRErasureEndpoint:
    def test_erasure_requires_confirm(self, db, user, member):
        """confirm=False yoki yo'q → 400."""
        client = Client()
        client.force_login(user)
        resp = client.post(
            reverse('accounts:gdpr_erasure'), data={}, content_type='application/json'
        )
        assert resp.status_code == 400

    def test_erasure_anonymizes_user_pii(self, db, user, member):
        """Erasure user.email/username/phone'ni redact qiladi."""
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user_id = user.id
        original_email = user.email

        client = Client()
        client.force_login(user)
        resp = client.post(
            reverse('accounts:gdpr_erasure'),
            data={'confirm': True},
            content_type='application/json',
        )
        assert resp.status_code == 200

        data = resp.json()
        assert 'redacted_models' in data
        assert 'CustomUser' in data['redacted_models']

        # User PII anonimlashtirildi
        u = User.objects.get(id=user_id)
        assert u.email != original_email
        assert u.email.startswith('redacted-')
        assert u.is_active is False

    def test_erasure_preserves_wallet_audit(self, db, user, member):
        """Erasure WalletTransaction'larni o'chirmaydi, faqat redact qiladi."""
        from apps.commerce.models import WalletTransaction

        w, _ = Wallet.objects.get_or_create(user=user)
        tx = wallet_service.top_up(w, 50, description='Audit-critical financial record')
        tx_id = tx.id
        amount = tx.coins_delta

        client = Client()
        client.force_login(user)
        client.post(
            reverse('accounts:gdpr_erasure'),
            data={'confirm': True},
            content_type='application/json',
        )

        # Audit row'i hali ham mavjud
        tx_after = WalletTransaction.objects.get(id=tx_id)
        assert tx_after.is_deleted is True
        assert tx_after.pii_redacted is True
        assert tx_after.description == ''  # PII redacted
        assert tx_after.coins_delta == amount  # financial audit saqlangan

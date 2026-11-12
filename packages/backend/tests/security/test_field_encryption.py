"""C18 — secrets stored in the database are encrypted at rest and masked in the admin.

Covers AIProviderConfig.api_key and WebhookEndpoint.secret: ciphertext in the row,
plaintext only in memory, key rotation (MultiFernet), and an admin that never renders
the secret (neither plaintext nor ciphertext).
"""

import logging

import pytest
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.urls import reverse

from apps.catalog.models import AIProviderConfig
from apps.webhooks.models import WebhookEndpoint
from core import crypto
from core.tenant import tenant_context

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()
PLAIN_API_KEY = 'sk-ant-api03-PLAINTEXT-SECRET-0123456789-WXYZ'
PLAIN_HOOK_SECRET = 'whsec-PLAINTEXT-0123456789abcdefghijklmnopqrstuv'


def _raw(table: str, column: str, pk) -> str:
    with connection.cursor() as cur:
        cur.execute(f'SELECT {column} FROM {table} WHERE id = %s', [pk])  # noqa: S608
        return cur.fetchone()[0]


@pytest.fixture
def endpoint(org):
    with tenant_context(org):
        return WebhookEndpoint.objects.create(
            organization=org,
            name='hook',
            url='https://example.com/hook',
            secret=PLAIN_HOOK_SECRET,
            events=['exam.submitted'],
        )


@pytest.fixture
def ai_config(db):
    return AIProviderConfig.objects.create(
        name='main', provider='claude', model='claude-sonnet-5-5', api_key=PLAIN_API_KEY
    )


# ── Ciphertext at rest ──────────────────────────────────────


@pytest.mark.django_db
class TestCiphertextAtRest:
    def test_webhook_secret_row_is_ciphertext(self, endpoint):
        raw = _raw('webhooks_webhookendpoint', 'secret', endpoint.pk)
        assert raw != PLAIN_HOOK_SECRET
        assert PLAIN_HOOK_SECRET not in raw
        assert crypto.is_encrypted(raw)

    def test_webhook_secret_reads_back_as_plaintext(self, endpoint, org):
        with tenant_context(org):
            fresh = WebhookEndpoint.objects.get(pk=endpoint.pk)
        assert fresh.secret == PLAIN_HOOK_SECRET

    def test_default_webhook_secret_is_encrypted_too(self, org):
        with tenant_context(org):
            ep = WebhookEndpoint.objects.create(organization=org, name='d', url='https://e.com/')
        raw = _raw('webhooks_webhookendpoint', 'secret', ep.pk)
        assert crypto.is_encrypted(raw)
        assert len(ep.secret) == 64 and ep.secret not in raw

    def test_ai_api_key_row_is_ciphertext(self, ai_config):
        raw = _raw('catalog_aiproviderconfig', 'api_key', ai_config.pk)
        assert crypto.is_encrypted(raw) and PLAIN_API_KEY not in raw
        assert AIProviderConfig.objects.get(pk=ai_config.pk).get_api_key() == PLAIN_API_KEY

    def test_signature_uses_the_plaintext_secret(self, endpoint, org):
        from apps.webhooks.services import sign_payload

        with tenant_context(org):
            fresh = WebhookEndpoint.objects.get(pk=endpoint.pk)
        assert sign_payload(b'{}', fresh.secret) == sign_payload(b'{}', PLAIN_HOOK_SECRET)

    def test_data_migration_encrypts_legacy_plaintext_rows(self, endpoint):
        with connection.cursor() as cur:
            cur.execute(
                'UPDATE webhooks_webhookendpoint SET secret = %s WHERE id = %s',
                [PLAIN_HOOK_SECRET, endpoint.pk],
            )
        from importlib import import_module

        from django.apps import apps as global_apps

        mig = import_module('apps.webhooks.migrations.0003_encrypt_endpoint_secret')
        mig.encrypt_existing_secrets(global_apps, None)
        raw = _raw('webhooks_webhookendpoint', 'secret', endpoint.pk)
        assert crypto.is_encrypted(raw) and crypto.decrypt(raw) == PLAIN_HOOK_SECRET


# ── Key rotation ────────────────────────────────────────────


@pytest.mark.django_db
class TestKeyRotation:
    def test_old_key_still_decrypts_after_a_new_primary_is_added(self):
        with override_settings(FIELD_ENCRYPTION_KEYS=[KEY_A]):
            token = crypto.encrypt('s3cret')
        with override_settings(FIELD_ENCRYPTION_KEYS=[KEY_B, KEY_A]):
            assert crypto.decrypt(token) == 's3cret'

    def test_removed_key_no_longer_decrypts(self):
        with override_settings(FIELD_ENCRYPTION_KEYS=[KEY_A]):
            token = crypto.encrypt('s3cret')
        with override_settings(FIELD_ENCRYPTION_KEYS=[KEY_B]):
            with pytest.raises(crypto.DecryptionError):
                crypto.decrypt(token)

    def test_legacy_single_key_setting_is_still_honoured(self):
        with override_settings(FIELD_ENCRYPTION_KEYS=[], FIELD_ENCRYPTION_KEY=KEY_A):
            token = crypto.encrypt('s3cret')
        with override_settings(FIELD_ENCRYPTION_KEYS=[KEY_A]):
            assert crypto.decrypt(token) == 's3cret'

    def test_rotate_command_reencrypts_every_secret_with_the_primary_key(
        self, endpoint, ai_config, org
    ):
        with override_settings(FIELD_ENCRYPTION_KEYS=[KEY_A]):
            # rewrite both rows under key A
            with tenant_context(org):
                ep = WebhookEndpoint.objects.get(pk=endpoint.pk)
                ep.secret = PLAIN_HOOK_SECRET
                ep.save()
            cfg = AIProviderConfig.objects.get(pk=ai_config.pk)
            cfg.api_key = PLAIN_API_KEY
            cfg.save()
        with override_settings(FIELD_ENCRYPTION_KEYS=[KEY_B, KEY_A]):
            call_command('rotate_field_encryption')
        with override_settings(FIELD_ENCRYPTION_KEYS=[KEY_B]):  # A retired
            assert crypto.decrypt(_raw('webhooks_webhookendpoint', 'secret', endpoint.pk)) == (
                PLAIN_HOOK_SECRET
            )
            assert crypto.decrypt(_raw('catalog_aiproviderconfig', 'api_key', ai_config.pk)) == (
                PLAIN_API_KEY
            )

    def test_explicit_key_check_fires_only_without_a_configured_key(self):
        from core.checks import check_field_encryption_key

        with override_settings(FIELD_ENCRYPTION_KEYS=[], FIELD_ENCRYPTION_KEY=''):
            assert [m.id for m in check_field_encryption_key(None)] == ['core.W010']
        with override_settings(FIELD_ENCRYPTION_KEYS=[KEY_A]):
            assert check_field_encryption_key(None) == []
        with override_settings(FIELD_ENCRYPTION_KEYS=['not-a-fernet-key']):
            assert [m.id for m in check_field_encryption_key(None)] == ['core.E010']


# ── Admin masking / no leaks ────────────────────────────────


@pytest.mark.django_db
class TestAdminMasking:
    def _client(self, client, superuser):
        client.force_login(superuser)
        return client

    def test_ai_config_change_page_shows_neither_plaintext_nor_ciphertext(
        self, client, superuser, ai_config
    ):
        raw = _raw('catalog_aiproviderconfig', 'api_key', ai_config.pk)
        url = reverse('admin:catalog_aiproviderconfig_change', args=[ai_config.pk])
        body = self._client(client, superuser).get(url).content.decode()
        assert PLAIN_API_KEY not in body
        assert raw not in body and crypto.PREFIX not in body
        assert 'WXYZ' in body  # last-4 hint is shown

    def test_ai_config_blank_submit_keeps_the_key(self, client, superuser, ai_config):
        url = reverse('admin:catalog_aiproviderconfig_change', args=[ai_config.pk])
        data = {
            'name': 'main',
            'provider': 'claude',
            'model': 'claude-sonnet-5-5',
            'use_for': 'all',
            'priority': 1,
            'is_active': 'on',
            'api_key': '',
            'base_url': '',
            'extra_options': '{}',
        }
        resp = self._client(client, superuser).post(url, data)
        assert resp.status_code == 302, resp.content.decode()[:2000]
        assert AIProviderConfig.objects.get(pk=ai_config.pk).get_api_key() == PLAIN_API_KEY

    def test_webhook_change_page_does_not_render_the_secret(self, client, superuser, endpoint):
        raw = _raw('webhooks_webhookendpoint', 'secret', endpoint.pk)
        url = reverse('admin:webhooks_webhookendpoint_change', args=[endpoint.pk])
        body = self._client(client, superuser).get(url).content.decode()
        assert PLAIN_HOOK_SECRET not in body
        assert raw not in body

    def test_repr_and_str_do_not_leak(self, endpoint, ai_config):
        for obj in (endpoint, ai_config):
            text = f'{obj!s} {obj!r}'
            assert PLAIN_HOOK_SECRET not in text and PLAIN_API_KEY not in text

    def test_webhook_delivery_does_not_log_the_secret(self, endpoint, org, caplog, monkeypatch):
        from apps.webhooks import tasks
        from apps.webhooks.models import WebhookDelivery

        class _Resp:
            status_code = 500
            text = 'boom'

        monkeypatch.setattr(tasks, 'safe_post', lambda *a, **k: _Resp())
        with tenant_context(org):
            delivery = WebhookDelivery.objects.create(
                endpoint=endpoint, event='exam.submitted', payload={'a': 1}
            )
        caplog.set_level(logging.DEBUG)
        try:
            tasks.deliver_webhook.apply(args=[str(delivery.pk)])
        except Exception:  # noqa: S110 — only the log output matters here
            pass
        assert PLAIN_HOOK_SECRET not in caplog.text

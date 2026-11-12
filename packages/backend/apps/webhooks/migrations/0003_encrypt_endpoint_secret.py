"""C18: WebhookEndpoint.secret encrypted at rest (core.fields.EncryptedTextField)."""

from django.db import migrations

import apps.webhooks.models
import core.fields


def encrypt_existing_secrets(apps, schema_editor):
    """Encrypt plaintext rows. Raw SQL: the historical model would decrypt/encrypt itself,
    and RLS would hide other tenants' rows from a filtered ORM query."""
    from django.db import connection

    from core import crypto

    conn = schema_editor.connection if schema_editor is not None else connection
    with conn.cursor() as cur:
        cur.execute("SELECT id, secret FROM webhooks_webhookendpoint WHERE secret NOT LIKE 'enc1:%%'")
        for pk, secret in cur.fetchall():
            cur.execute(
                'UPDATE webhooks_webhookendpoint SET secret = %s WHERE id = %s',
                [crypto.encrypt(secret), pk],
            )


def decrypt_secrets(apps, schema_editor):
    from core import crypto

    with schema_editor.connection.cursor() as cur:
        cur.execute("SELECT id, secret FROM webhooks_webhookendpoint WHERE secret LIKE 'enc1:%%'")
        for pk, secret in cur.fetchall():
            cur.execute(
                'UPDATE webhooks_webhookendpoint SET secret = %s WHERE id = %s',
                [crypto.decrypt(secret), pk],
            )


class Migration(migrations.Migration):
    dependencies = [('webhooks', '0002_enable_rls')]

    operations = [
        migrations.AlterField(
            model_name='webhookendpoint',
            name='secret',
            field=core.fields.EncryptedTextField(default=apps.webhooks.models._generate_secret),
        ),
        migrations.RunPython(encrypt_existing_secrets, decrypt_secrets),
    ]

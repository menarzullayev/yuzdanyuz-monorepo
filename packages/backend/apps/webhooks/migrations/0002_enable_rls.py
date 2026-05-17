"""ISSUE-110 W2 — webhooks_webhookendpoint RLS.

`WebhookEndpoint` `TenantTimestampMixin` (L1/L2) ishlatadi, lekin L3 (PostgreSQL RLS)
qatlami yo'q edi. Defense-in-depth uchun commerce/analytics pattern bo'yicha RLS
yoqamiz.

`WebhookDelivery` o'zining `organization` field'iga ega emas — parent FK
(`endpoint`) orqali bog'lanadi. Through-table policy `endpoint_id IN (SELECT ...)`
pattern (catalog_questiondraft + exams_mockexamquestion bilan teng).
"""

from django.db import migrations


def enable_rls(apps, schema_editor):
    cursor = schema_editor.connection.cursor()

    cursor.execute('ALTER TABLE webhooks_webhookendpoint ENABLE ROW LEVEL SECURITY;')
    cursor.execute('ALTER TABLE webhooks_webhookdelivery ENABLE ROW LEVEL SECURITY;')

    endpoint_policies = [
        ('select', "organization_id = current_setting('app.current_org_id')::uuid"),
        ('insert', "organization_id = current_setting('app.current_org_id')::uuid"),
        ('update', "organization_id = current_setting('app.current_org_id')::uuid"),
        ('delete', "organization_id = current_setting('app.current_org_id')::uuid"),
    ]
    for op, clause in endpoint_policies:
        name = f'webhookendpoint_org_{op}'
        cursor.execute(f'DROP POLICY IF EXISTS "{name}" ON webhooks_webhookendpoint;')
        if op == 'insert':
            cursor.execute(
                f'CREATE POLICY "{name}" ON webhooks_webhookendpoint '
                f'FOR INSERT WITH CHECK ({clause});'
            )
        else:
            cursor.execute(
                f'CREATE POLICY "{name}" ON webhooks_webhookendpoint '
                f'FOR {op.upper()} USING ({clause});'
            )

    delivery_clause = (
        'endpoint_id IN (SELECT id FROM webhooks_webhookendpoint '
        "WHERE organization_id = current_setting('app.current_org_id')::uuid)"
    )
    for op in ('select', 'insert', 'update', 'delete'):
        name = f'webhookdelivery_org_{op}'
        cursor.execute(f'DROP POLICY IF EXISTS "{name}" ON webhooks_webhookdelivery;')
        if op == 'insert':
            cursor.execute(
                f'CREATE POLICY "{name}" ON webhooks_webhookdelivery '
                f'FOR INSERT WITH CHECK ({delivery_clause});'
            )
        else:
            cursor.execute(
                f'CREATE POLICY "{name}" ON webhooks_webhookdelivery '
                f'FOR {op.upper()} USING ({delivery_clause});'
            )


def disable_rls(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    for table in ('webhooks_webhookdelivery', 'webhooks_webhookendpoint'):
        cursor.execute(f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;')


class Migration(migrations.Migration):
    dependencies = [('webhooks', '0001_initial')]
    operations = [migrations.RunPython(enable_rls, disable_rls)]

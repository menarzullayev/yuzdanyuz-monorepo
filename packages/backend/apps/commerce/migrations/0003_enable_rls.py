"""ISSUE-303 — Enable RLS on commerce tenant tables.

Scope:
  - commerce_organizationsubscription — org-scoped (organization FK)

Skipped (intentional):
  - commerce_wallet, commerce_wallettransaction — user-scoped (no org FK)
  - commerce_paymentintent — user-scoped (target_organization is buyer side,
    not row owner)
  - commerce_subscriptionplan — global (platform-level products)
  - commerce_referral*, commerce_affiliate* — user-scoped
"""

from django.db import migrations


def enable_rls(apps, schema_editor):
    tables = ['commerce_organizationsubscription']
    sql_template = """
        ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS "{table}_org_select" ON {table};
        CREATE POLICY "{table}_org_select" ON {table}
            FOR SELECT USING (
                organization_id = current_setting('app.current_org_id', true)::uuid
            );
        DROP POLICY IF EXISTS "{table}_org_insert" ON {table};
        CREATE POLICY "{table}_org_insert" ON {table}
            FOR INSERT WITH CHECK (
                organization_id = current_setting('app.current_org_id', true)::uuid
            );
        DROP POLICY IF EXISTS "{table}_org_update" ON {table};
        CREATE POLICY "{table}_org_update" ON {table}
            FOR UPDATE USING (
                organization_id = current_setting('app.current_org_id', true)::uuid
            );
        DROP POLICY IF EXISTS "{table}_org_delete" ON {table};
        CREATE POLICY "{table}_org_delete" ON {table}
            FOR DELETE USING (
                organization_id = current_setting('app.current_org_id', true)::uuid
            );
    """
    with schema_editor.connection.cursor() as cursor:
        for table in tables:
            try:
                cursor.execute(sql_template.format(table=table))
            except Exception as e:
                print(f'⚠️  {table}: {e}')


def disable_rls(apps, schema_editor):
    tables = ['commerce_organizationsubscription']
    with schema_editor.connection.cursor() as cursor:
        for table in tables:
            try:
                cursor.execute(f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;')
            except Exception as e:
                print(f'⚠️  {table}: {e}')


class Migration(migrations.Migration):
    dependencies = [
        ('commerce', '0002_issue_103_soft_delete'),
    ]
    operations = [migrations.RunPython(enable_rls, reverse_code=disable_rls)]

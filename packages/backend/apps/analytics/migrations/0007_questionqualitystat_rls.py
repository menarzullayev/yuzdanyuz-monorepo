"""RLS for analytics_questionqualitystat (same policies as 0004_enable_rls)."""

from django.db import migrations

TABLE = 'analytics_questionqualitystat'
ORG = "NULLIF(current_setting('app.current_org_id', true), '')::uuid"


def enable_rls(apps, schema_editor):
    sql = f"""
        ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS "{TABLE}_org_select" ON {TABLE};
        CREATE POLICY "{TABLE}_org_select" ON {TABLE}
            FOR SELECT USING (organization_id = {ORG});
        DROP POLICY IF EXISTS "{TABLE}_org_insert" ON {TABLE};
        CREATE POLICY "{TABLE}_org_insert" ON {TABLE}
            FOR INSERT WITH CHECK (organization_id = {ORG});
        DROP POLICY IF EXISTS "{TABLE}_org_update" ON {TABLE};
        CREATE POLICY "{TABLE}_org_update" ON {TABLE}
            FOR UPDATE USING (organization_id = {ORG});
        DROP POLICY IF EXISTS "{TABLE}_org_delete" ON {TABLE};
        CREATE POLICY "{TABLE}_org_delete" ON {TABLE}
            FOR DELETE USING (organization_id = {ORG});
    """
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(sql)


def disable_rls(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f'ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY;')


class Migration(migrations.Migration):
    dependencies = [('analytics', '0006_questionqualitystat')]
    operations = [migrations.RunPython(enable_rls, reverse_code=disable_rls)]

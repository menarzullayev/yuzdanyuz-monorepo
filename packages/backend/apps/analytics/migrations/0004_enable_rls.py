"""ISSUE-303 — Enable RLS on analytics tenant tables."""

from django.db import migrations


def enable_rls(apps, schema_editor):
    tables = ['analytics_examevent', 'analytics_reportexport']
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
    tables = ['analytics_examevent', 'analytics_reportexport']
    with schema_editor.connection.cursor() as cursor:
        for table in tables:
            try:
                cursor.execute(f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;')
            except Exception as e:
                print(f'⚠️  {table}: {e}')


class Migration(migrations.Migration):
    dependencies = [
        ('analytics', '0003_issue_103b_fk_set_null'),
    ]
    operations = [migrations.RunPython(enable_rls, reverse_code=disable_rls)]

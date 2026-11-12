"""RLS for the B2B tenant tables (organization_id based), per catalog 0010 pattern."""

from django.db import migrations

TABLES = {
    'organizations_group': 'group',
    'organizations_groupmember': 'groupmember',
    'organizations_lesson': 'lesson',
}
ORG = "organization_id = current_setting('app.current_org_id')::uuid"


def enable_rls(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    for table, short in TABLES.items():
        cursor.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;')
        for op, clause in [
            ('select', f'FOR SELECT USING ({ORG})'),
            ('insert', f'FOR INSERT WITH CHECK ({ORG})'),
            ('update', f'FOR UPDATE USING ({ORG})'),
            ('delete', f'FOR DELETE USING ({ORG})'),
        ]:
            name = f'{short}_org_{op}'
            cursor.execute(f'DROP POLICY IF EXISTS "{name}" ON {table};')
            cursor.execute(f'CREATE POLICY "{name}" ON {table} {clause};')


def disable_rls(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    for table, short in TABLES.items():
        for op in ('select', 'insert', 'update', 'delete'):
            cursor.execute(f'DROP POLICY IF EXISTS "{short}_org_{op}" ON {table};')
        cursor.execute(f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;')


class Migration(migrations.Migration):
    dependencies = [('organizations', '0004_b2b_models')]
    operations = [migrations.RunPython(enable_rls, disable_rls)]

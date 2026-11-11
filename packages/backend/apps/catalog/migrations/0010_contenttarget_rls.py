"""RLS policies for catalog_contenttarget (tenant table, organization_id based)."""

from django.db import migrations

TABLE = 'catalog_contenttarget'
ORG = "organization_id = current_setting('app.current_org_id')::uuid"


def enable_rls(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    cursor.execute(f'ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY;')
    for op, clause in [
        ('select', f'FOR SELECT USING ({ORG})'),
        ('insert', f'FOR INSERT WITH CHECK ({ORG})'),
        ('update', f'FOR UPDATE USING ({ORG})'),
        ('delete', f'FOR DELETE USING ({ORG})'),
    ]:
        name = f'contenttarget_org_{op}'
        cursor.execute(f'DROP POLICY IF EXISTS "{name}" ON {TABLE};')
        cursor.execute(f'CREATE POLICY "{name}" ON {TABLE} {clause};')


def disable_rls(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    for op in ('select', 'insert', 'update', 'delete'):
        cursor.execute(f'DROP POLICY IF EXISTS "contenttarget_org_{op}" ON {TABLE};')
    cursor.execute(f'ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY;')


class Migration(migrations.Migration):
    dependencies = [('catalog', '0009_contenttarget')]
    operations = [migrations.RunPython(enable_rls, disable_rls)]

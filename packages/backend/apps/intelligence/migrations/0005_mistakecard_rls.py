"""
RLS for intelligence_mistakecard (pattern: exams.0002 + 0007 delete policies).

Strict on purpose: no try/except, a failure aborts the migration instead of
silently leaving the table unprotected.
"""

from django.db import migrations

TABLE = 'intelligence_mistakecard'
ORG = "organization_id = current_setting('app.current_org_id')::uuid"


def enable_rls(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f'ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY')
        for cmd, clause in (
            ('select', f'FOR SELECT USING ({ORG})'),
            ('insert', f'FOR INSERT WITH CHECK ({ORG})'),
            ('update', f'FOR UPDATE USING ({ORG})'),
            ('delete', f'FOR DELETE USING ({ORG})'),
        ):
            name = f'mistakecard_org_{cmd}'
            cursor.execute(f'DROP POLICY IF EXISTS "{name}" ON {TABLE}')
            cursor.execute(f'CREATE POLICY "{name}" ON {TABLE} {clause}')


def disable_rls(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for cmd in ('select', 'insert', 'update', 'delete'):
            cursor.execute(f'DROP POLICY IF EXISTS "mistakecard_org_{cmd}" ON {TABLE}')
        cursor.execute(f'ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [('intelligence', '0004_mistakecard')]

    operations = [migrations.RunPython(enable_rls, disable_rls)]

"""RLS for the sitting tables (pattern: catalog/0003_enable_rls.py + exams/0007 DELETE policies).

Public certificate verification runs with no tenant context and reads through
unscoped_context() — same convention as the commerce webhooks; no extra policy is added.
Policies use organization_id only (all five tables carry the column).
"""

from django.db import migrations

TABLES = {
    'exams_gradescale': 'gradescale',
    'exams_examsitting': 'examsitting',
    'exams_sittingitem': 'sittingitem',
    'exams_sittingresult': 'sittingresult',
    'exams_certificate': 'certificate',
}
ORG = "organization_id = current_setting('app.current_org_id')::uuid"


def enable_rls(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    for table, name in TABLES.items():
        cursor.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;')
        for cmd, clause in (
            ('select', f'FOR SELECT USING ({ORG})'),
            ('insert', f'FOR INSERT WITH CHECK ({ORG})'),
            ('update', f'FOR UPDATE USING ({ORG})'),
            ('delete', f'FOR DELETE USING ({ORG})'),
        ):
            policy = f'{name}_org_{cmd}'
            cursor.execute(f'DROP POLICY IF EXISTS "{policy}" ON {table};')
            cursor.execute(f'CREATE POLICY "{policy}" ON {table} {clause};')


def disable_rls(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    for table, name in TABLES.items():
        for cmd in ('select', 'insert', 'update', 'delete'):
            cursor.execute(f'DROP POLICY IF EXISTS "{name}_org_{cmd}" ON {table};')
        cursor.execute(f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;')


class Migration(migrations.Migration):
    dependencies = [('exams', '0008_sittings')]
    operations = [migrations.RunPython(enable_rls, disable_rls)]

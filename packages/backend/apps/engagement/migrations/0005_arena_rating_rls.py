"""RLS for the engagement tenant tables (pattern: exams/0009_sittings_rls.py).

Typing tables are platform-wide and user-scoped, so they get no policy.
The cross-tenant school rating reads through unscoped_context() like the public
certificate verification does.
"""

from django.db import migrations

TABLES = {
    'engagement_quiz': 'quiz',
    'engagement_quizquestion': 'quizquestion',
    'engagement_quizplay': 'quizplay',
    'engagement_quizplayanswer': 'quizplayanswer',
    'engagement_orgratingprofile': 'orgratingprofile',
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
    dependencies = [('engagement', '0004_arena_rating_typing')]
    operations = [migrations.RunPython(enable_rls, disable_rls)]

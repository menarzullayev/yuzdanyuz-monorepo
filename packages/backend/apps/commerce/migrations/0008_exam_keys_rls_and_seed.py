"""RLS for the B2B exam key tables + seed of the default exam types.

RLS follows apps/catalog/migrations/0003_enable_rls.py (organization_id =
current_setting('app.current_org_id')). Skipped on purpose: commerce_userSubscription,
commerce_examaccessuse, promo tables (user-scoped / global, like the wallet tables).
"""

from django.db import migrations

TABLES = ['commerce_examkeybatch', 'commerce_examkey']

SQL = """
    ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS "{t}_org_select" ON {t};
    CREATE POLICY "{t}_org_select" ON {t}
        FOR SELECT USING (organization_id = current_setting('app.current_org_id', true)::uuid);
    DROP POLICY IF EXISTS "{t}_org_insert" ON {t};
    CREATE POLICY "{t}_org_insert" ON {t}
        FOR INSERT WITH CHECK (organization_id = current_setting('app.current_org_id', true)::uuid);
    DROP POLICY IF EXISTS "{t}_org_update" ON {t};
    CREATE POLICY "{t}_org_update" ON {t}
        FOR UPDATE USING (organization_id = current_setting('app.current_org_id', true)::uuid);
    DROP POLICY IF EXISTS "{t}_org_delete" ON {t};
    CREATE POLICY "{t}_org_delete" ON {t}
        FOR DELETE USING (organization_id = current_setting('app.current_org_id', true)::uuid);
"""


def enable_rls(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cur:
        for t in TABLES:
            cur.execute(SQL.format(t=t))


def disable_rls(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cur:
        for t in TABLES:
            cur.execute(f'ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;')


def seed_exam_types(apps, schema_editor):
    ExamType = apps.get_model('commerce', 'ExamType')
    for order, (code, name) in enumerate(
        [('dtm', 'DTM'), ('milliy-sertifikat', 'Milliy sertifikat')]
    ):
        ExamType.objects.get_or_create(code=code, defaults={'name': name, 'sort_order': order})


class Migration(migrations.Migration):
    dependencies = [('commerce', '0007_billing_monetisation')]
    operations = [
        migrations.RunPython(enable_rls, reverse_code=disable_rls),
        migrations.RunPython(seed_exam_types, reverse_code=migrations.RunPython.noop),
    ]

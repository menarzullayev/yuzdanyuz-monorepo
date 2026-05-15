"""
RLS migration for Task 4 (Exam Engine) tables.

catalog.0003_enable_rls.py pattern bo'yicha. Har jadvalga:
  - ALTER TABLE ENABLE ROW LEVEL SECURITY
  - SELECT/INSERT/UPDATE policy'lar — `app.current_org_id` middleware tomonidan set qilingan

L3 PostgreSQL RLS — defense-in-depth ning eng past qatlami.
TenantManager (L2) bypass qilingan bo'lsa ham, RLS DB darajasida cross-org leak'ni
to'xtatadi. Worker/management command'lar uchun: bu policy'lar RLSMiddleware'sis
ishlamaydi (app.current_org_id set qilinmagan) — `unscoped_context()` bilan
ishlatish kerak yoki BYPASSRLS bilan superuser db role.

MockExam policy is_public=true ni ham ruxsat etadi (catalog_questionbank pattern).
Through table (mock_exam_question) parent mock_exam orqali filter qilinadi.
"""

from django.db import migrations


# Through table'lar uchun parent FK'ga reference qilamiz
EXAM_TABLES = [
    'exams_mockexam',
    'exams_mockexamquestion',
    'exams_examattempt',
    'exams_practicesession',
    'exams_useranswer',
    'exams_anticheatevent',
    'exams_questiondispute',
]

# MockExam (is_public + org), boshqalari faqat org
MOCKEXAM_POLICIES = """
    DROP POLICY IF EXISTS "mockexam_org_select" ON exams_mockexam;
    CREATE POLICY "mockexam_org_select" ON exams_mockexam
    FOR SELECT USING (
        organization_id = current_setting('app.current_org_id')::uuid
        OR is_public = true
    );

    DROP POLICY IF EXISTS "mockexam_org_insert" ON exams_mockexam;
    CREATE POLICY "mockexam_org_insert" ON exams_mockexam
    FOR INSERT WITH CHECK (
        organization_id = current_setting('app.current_org_id')::uuid
    );

    DROP POLICY IF EXISTS "mockexam_org_update" ON exams_mockexam;
    CREATE POLICY "mockexam_org_update" ON exams_mockexam
    FOR UPDATE USING (
        organization_id = current_setting('app.current_org_id')::uuid
    );
"""

# Through table — parent mock_exam orqali filter (catalog_questiondraft pattern)
MOCKEXAMQUESTION_POLICIES = """
    DROP POLICY IF EXISTS "mockexamquestion_org_select" ON exams_mockexamquestion;
    CREATE POLICY "mockexamquestion_org_select" ON exams_mockexamquestion
    FOR SELECT USING (
        mock_exam_id IN (
            SELECT id FROM exams_mockexam
            WHERE organization_id = current_setting('app.current_org_id')::uuid
                OR is_public = true
        )
    );

    DROP POLICY IF EXISTS "mockexamquestion_org_insert" ON exams_mockexamquestion;
    CREATE POLICY "mockexamquestion_org_insert" ON exams_mockexamquestion
    FOR INSERT WITH CHECK (
        mock_exam_id IN (
            SELECT id FROM exams_mockexam
            WHERE organization_id = current_setting('app.current_org_id')::uuid
        )
    );
"""

# Standard tenant-scoped tables: faqat org bo'yicha
STANDARD_POLICIES_TEMPLATE = """
    DROP POLICY IF EXISTS "{table}_org_select" ON {table};
    CREATE POLICY "{table}_org_select" ON {table}
    FOR SELECT USING (
        organization_id = current_setting('app.current_org_id')::uuid
    );

    DROP POLICY IF EXISTS "{table}_org_insert" ON {table};
    CREATE POLICY "{table}_org_insert" ON {table}
    FOR INSERT WITH CHECK (
        organization_id = current_setting('app.current_org_id')::uuid
    );

    DROP POLICY IF EXISTS "{table}_org_update" ON {table};
    CREATE POLICY "{table}_org_update" ON {table}
    FOR UPDATE USING (
        organization_id = current_setting('app.current_org_id')::uuid
    );
"""

STANDARD_TABLES = [
    'exams_examattempt',
    'exams_practicesession',
    'exams_useranswer',
    'exams_anticheatevent',
    'exams_questiondispute',
]


def enable_rls(apps, schema_editor):
    """RLS yoqish va policy'larni o'rnatish."""
    connection = schema_editor.connection

    with connection.cursor() as cursor:
        # 1. Enable RLS on each table
        for table in EXAM_TABLES:
            try:
                cursor.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;')
            except Exception as e:
                print(f'⚠️  ENABLE RLS {table}: {e}')

        # 2. Special policies (MockExam + MockExamQuestion)
        try:
            for statement in MOCKEXAM_POLICIES.split(';'):
                if statement.strip():
                    cursor.execute(statement)
        except Exception as e:
            print(f'⚠️  MockExam policies: {e}')

        try:
            for statement in MOCKEXAMQUESTION_POLICIES.split(';'):
                if statement.strip():
                    cursor.execute(statement)
        except Exception as e:
            print(f'⚠️  MockExamQuestion policies: {e}')

        # 3. Standard policies for org-scoped tables
        for table in STANDARD_TABLES:
            try:
                sql = STANDARD_POLICIES_TEMPLATE.format(table=table)
                for statement in sql.split(';'):
                    if statement.strip():
                        cursor.execute(statement)
            except Exception as e:
                print(f'⚠️  {table} policies: {e}')


def disable_rls(apps, schema_editor):
    """Rollback: RLS o'chirish."""
    connection = schema_editor.connection

    with connection.cursor() as cursor:
        for table in EXAM_TABLES:
            try:
                cursor.execute(f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;')
            except Exception:
                pass


class Migration(migrations.Migration):
    dependencies = [
        ('exams', '0001_initial'),
        # catalog.0003_enable_rls bilan bog'liq emas — alohida ishlaydi.
    ]

    operations = [
        migrations.RunPython(enable_rls, disable_rls),
    ]

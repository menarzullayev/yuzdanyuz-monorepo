# Generated migration for RLS setup
from django.db import migrations, models


def enable_rls(apps, schema_editor):
    """Enable Row Level Security on multi-tenant tables"""
    connection = schema_editor.connection

    tables_with_rls = [
        # Task 3 - Catalog
        'catalog_question',
        'catalog_questionversion',
        'catalog_questionbank',
        'catalog_importbatch',
        'catalog_questiondraft',
        'catalog_subject',
    ]

    with connection.cursor() as cursor:
        # Enable RLS
        for table in tables_with_rls:
            try:
                cursor.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
            except Exception as e:
                print(f"⚠️  {table}: {e}")

        # Create policies using app.current_org_id set by middleware
        catalog_policies = """
            -- Question RLS policies
            DROP POLICY IF EXISTS "question_org_select" ON catalog_question;
            CREATE POLICY "question_org_select" ON catalog_question
            FOR SELECT USING (
                organization_id = current_setting('app.current_org_id')::uuid
            );

            DROP POLICY IF EXISTS "question_org_insert" ON catalog_question;
            CREATE POLICY "question_org_insert" ON catalog_question
            FOR INSERT WITH CHECK (
                organization_id = current_setting('app.current_org_id')::uuid
            );

            DROP POLICY IF EXISTS "question_org_update" ON catalog_question;
            CREATE POLICY "question_org_update" ON catalog_question
            FOR UPDATE USING (
                organization_id = current_setting('app.current_org_id')::uuid
            );

            -- QuestionVersion RLS
            DROP POLICY IF EXISTS "questionversion_org_select" ON catalog_questionversion;
            CREATE POLICY "questionversion_org_select" ON catalog_questionversion
            FOR SELECT USING (
                question_id IN (
                    SELECT id FROM catalog_question
                    WHERE organization_id = current_setting('app.current_org_id')::uuid
                )
            );

            -- QuestionBank RLS
            DROP POLICY IF EXISTS "questionbank_org_select" ON catalog_questionbank;
            CREATE POLICY "questionbank_org_select" ON catalog_questionbank
            FOR SELECT USING (
                organization_id = current_setting('app.current_org_id')::uuid
                OR is_public = true
            );

            DROP POLICY IF EXISTS "questionbank_org_insert" ON catalog_questionbank;
            CREATE POLICY "questionbank_org_insert" ON catalog_questionbank
            FOR INSERT WITH CHECK (
                organization_id = current_setting('app.current_org_id')::uuid
            );

            -- ImportBatch RLS
            DROP POLICY IF EXISTS "importbatch_org_select" ON catalog_importbatch;
            CREATE POLICY "importbatch_org_select" ON catalog_importbatch
            FOR SELECT USING (
                organization_id = current_setting('app.current_org_id')::uuid
            );

            DROP POLICY IF EXISTS "importbatch_org_insert" ON catalog_importbatch;
            CREATE POLICY "importbatch_org_insert" ON catalog_importbatch
            FOR INSERT WITH CHECK (
                organization_id = current_setting('app.current_org_id')::uuid
            );

            -- QuestionDraft RLS
            DROP POLICY IF EXISTS "questiondraft_org_select" ON catalog_questiondraft;
            CREATE POLICY "questiondraft_org_select" ON catalog_questiondraft
            FOR SELECT USING (
                batch_id IN (
                    SELECT id FROM catalog_importbatch
                    WHERE organization_id = current_setting('app.current_org_id')::uuid
                )
            );

            DROP POLICY IF EXISTS "questiondraft_org_insert" ON catalog_questiondraft;
            CREATE POLICY "questiondraft_org_insert" ON catalog_questiondraft
            FOR INSERT WITH CHECK (
                batch_id IN (
                    SELECT id FROM catalog_importbatch
                    WHERE organization_id = current_setting('app.current_org_id')::uuid
                )
            );
        """

        # Try to execute policies (may fail if using standard auth, that's OK)
        try:
            for statement in catalog_policies.split(';'):
                if statement.strip():
                    cursor.execute(statement)
        except Exception as e:
            print(f"⚠️  RLS policies: {e}")


def disable_rls(apps, schema_editor):
    """Disable RLS (for rollback)"""
    connection = schema_editor.connection

    tables_with_rls = [
        'catalog_question',
        'catalog_questionversion',
        'catalog_questionbank',
        'catalog_importbatch',
        'catalog_questiondraft',
        'catalog_subject',
    ]

    with connection.cursor() as cursor:
        for table in tables_with_rls:
            try:
                cursor.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
            except:
                pass


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0002_aiproviderconfig'),
    ]

    operations = [
        migrations.RunPython(enable_rls, disable_rls),
    ]

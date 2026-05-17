"""ISSUE-109 C3 + ISSUE-110 W9.

C3: `catalog_subject` RLS yoqilgan edi, lekin 0 ta policy mavjud. `Subject` model
`organization` nullable (platform-global fan), demak RLS noto'g'ri yoqilgan. Production'da
least-privilege role joriy qilinganda `Subject.objects.all()` silent bo'sh qaytaradi.
Bu yerda RLS'ni butunlay o'chiramiz.

W9: catalog jadvallarining mavjud RLS policy'lari faqat SELECT/INSERT/UPDATE qoplaydi.
commerce/analytics migration'lari bilan harmonize qilish uchun DELETE policy qo'shamiz.
"""

from django.db import migrations


def apply_fixes(apps, schema_editor):
    cursor = schema_editor.connection.cursor()

    # ── C3: Subject RLS ni o'chirish (organization nullable, platform-global) ──
    cursor.execute('ALTER TABLE catalog_subject DISABLE ROW LEVEL SECURITY;')

    # ── W9: DELETE policy'lar (commerce/analytics bilan harmonize) ──
    delete_policies = [
        (
            'question_org_delete',
            'catalog_question',
            "organization_id = current_setting('app.current_org_id')::uuid",
        ),
        (
            'questionbank_org_delete',
            'catalog_questionbank',
            "organization_id = current_setting('app.current_org_id')::uuid",
        ),
        (
            'importbatch_org_delete',
            'catalog_importbatch',
            "organization_id = current_setting('app.current_org_id')::uuid",
        ),
        (
            'questionversion_org_delete',
            'catalog_questionversion',
            'question_id IN (SELECT id FROM catalog_question WHERE organization_id = '
            "current_setting('app.current_org_id')::uuid)",
        ),
        (
            'questiondraft_org_delete',
            'catalog_questiondraft',
            'batch_id IN (SELECT id FROM catalog_importbatch WHERE organization_id = '
            "current_setting('app.current_org_id')::uuid)",
        ),
    ]
    for policy_name, table, using_clause in delete_policies:
        cursor.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON {table};')
        cursor.execute(
            f'CREATE POLICY "{policy_name}" ON {table} FOR DELETE USING ({using_clause});'
        )


def revert_fixes(apps, schema_editor):
    cursor = schema_editor.connection.cursor()

    # Subject RLS qaytarib yoqish (0003_enable_rls bilan teng holatga keltirish)
    cursor.execute('ALTER TABLE catalog_subject ENABLE ROW LEVEL SECURITY;')

    # DELETE policy'larni olib tashlash
    for policy_name, table in [
        ('question_org_delete', 'catalog_question'),
        ('questionbank_org_delete', 'catalog_questionbank'),
        ('importbatch_org_delete', 'catalog_importbatch'),
        ('questionversion_org_delete', 'catalog_questionversion'),
        ('questiondraft_org_delete', 'catalog_questiondraft'),
    ]:
        cursor.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON {table};')


class Migration(migrations.Migration):
    dependencies = [('catalog', '0006_historicalquestion')]
    operations = [migrations.RunPython(apply_fixes, revert_fixes)]

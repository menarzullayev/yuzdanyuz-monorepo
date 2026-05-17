"""ISSUE-110 W9 — exams jadvallari uchun DELETE policy.

0002_enable_rls.py faqat SELECT/INSERT/UPDATE qoplaydi. commerce/analytics migration'lari
DELETE policy ham yozadi — bir xil pattern uchun harmonize qilamiz.

MockExam (org yoki is_public=true ham SELECT'da edi, lekin DELETE faqat o'z org).
Through table (mock_exam_question) parent FK orqali.
"""

from django.db import migrations


DELETE_POLICIES = [
    (
        'mockexam_org_delete',
        'exams_mockexam',
        "organization_id = current_setting('app.current_org_id')::uuid",
    ),
    (
        'examattempt_org_delete',
        'exams_examattempt',
        "organization_id = current_setting('app.current_org_id')::uuid",
    ),
    (
        'practicesession_org_delete',
        'exams_practicesession',
        "organization_id = current_setting('app.current_org_id')::uuid",
    ),
    (
        'useranswer_org_delete',
        'exams_useranswer',
        "organization_id = current_setting('app.current_org_id')::uuid",
    ),
    (
        'anticheatevent_org_delete',
        'exams_anticheatevent',
        "organization_id = current_setting('app.current_org_id')::uuid",
    ),
    (
        'questiondispute_org_delete',
        'exams_questiondispute',
        "organization_id = current_setting('app.current_org_id')::uuid",
    ),
    (
        'mockexamquestion_org_delete',
        'exams_mockexamquestion',
        'mock_exam_id IN (SELECT id FROM exams_mockexam WHERE organization_id = '
        "current_setting('app.current_org_id')::uuid)",
    ),
]


def apply_delete_policies(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    for policy_name, table, using_clause in DELETE_POLICIES:
        cursor.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON {table};')
        cursor.execute(
            f'CREATE POLICY "{policy_name}" ON {table} FOR DELETE USING ({using_clause});'
        )


def remove_delete_policies(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    for policy_name, table, _ in DELETE_POLICIES:
        cursor.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON {table};')


class Migration(migrations.Migration):
    dependencies = [('exams', '0006_historicalexamattempt')]
    operations = [migrations.RunPython(apply_delete_policies, remove_delete_policies)]

"""B15: RLS that also holds for the table owner and for a non-owner runtime role.

* every policy reads the org through app_current_org_id() (NULL, not an error, when unset);
* commands without a policy get the table's org filter (they would be denied otherwise);
* each RLS table gets the `rls_bypass` policy for org-less sessions and FORCE RLS.

See core/rls.py and core/db_roles.py. Not reversible: the original expressions differ only
in raising instead of returning NULL when no org is set.
"""

from django.db import migrations


def harden(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    from core import rls

    with schema_editor.connection.cursor() as cur:
        rls.harden_existing(cur)


class Migration(migrations.Migration):
    dependencies = [
        ('ops', '0001_initial'),
        ('analytics', '0008_expense'),
        ('catalog', '0012_search_and_question_indexes'),
        ('commerce', '0008_exam_keys_rls_and_seed'),
        ('engagement', '0006_seed_typing_texts'),
        ('exams', '0010_billing_monetisation'),
        ('intelligence', '0006_grading_and_speaking'),
        ('organizations', '0008_platform_org_and_backfill'),
        ('webhooks', '0003_encrypt_endpoint_secret'),
        ('accounts', '0009_erasurerequest'),
    ]
    operations = [migrations.RunPython(harden, migrations.RunPython.noop)]

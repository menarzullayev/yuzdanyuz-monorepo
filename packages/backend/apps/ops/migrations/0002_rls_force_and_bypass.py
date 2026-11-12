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
    from psycopg2 import sql

    from core import rls
    from core.db_roles import bypass_default

    with schema_editor.connection.cursor() as cur:
        rls.harden_existing(cur)
        # Database-level default for connections that never run our connection_created hook
        # (psql, pgbouncer server connections). PostgreSQL only lets a superuser store a
        # custom parameter this way, so the owner role prints the SQL for the DBA instead.
        cur.execute('SELECT rolsuper FROM pg_roles WHERE rolname = current_user')
        if cur.fetchone()[0]:
            cur.execute('SELECT current_database()')
            db = cur.fetchone()[0]
            cur.execute(
                sql.SQL('ALTER DATABASE {} SET app.rls_bypass = {}').format(
                    sql.Identifier(db), sql.Literal(bypass_default())
                )
            )
        else:
            print(
                "\n  NOTE: as a superuser, run once: ALTER DATABASE <db> SET app.rls_bypass = 'on';"
                ' (needed only for sessions that bypass Django, e.g. pgbouncer server'
                ' connections; docs/deployment/database-roles.md)'
            )


class Migration(migrations.Migration):
    dependencies = [
        ('ops', '0001_initial'),
        ('analytics', '0008_expense'),
        ('catalog', '0011_aiproviderconfig_profiles_encryption'),
        ('commerce', '0008_exam_keys_rls_and_seed'),
        ('engagement', '0006_seed_typing_texts'),
        ('exams', '0010_billing_monetisation'),
        ('intelligence', '0006_grading_and_speaking'),
        ('organizations', '0008_platform_org_and_backfill'),
        ('webhooks', '0003_encrypt_endpoint_secret'),
        ('accounts', '0009_erasurerequest'),
    ]
    operations = [migrations.RunPython(harden, migrations.RunPython.noop)]

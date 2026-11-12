"""B15 stage 2: RLS enforced for the runtime role, with shared content and search.

* `app_rls_bypass()` is true only for the explicit value 'all', which core/db_rls.py sends
  on statements issued inside unscoped_context(); unset means no bypass (fail closed).
* Content shared on purpose stays readable across organisations: questions in a public
  question bank or used by a public mock exam (`app_question_shared`), SELECT only.
* `app_search_question_versions` searches question text with the trigram index (E01). RLS
  quals are security barriers, so a non-leakproof LIKE under RLS cannot use an index; the
  function runs as a NOLOGIN BYPASSRLS role and applies the same visibility rule itself.

The definer role (settings.DB_DEFINER_ROLE, default `yuzdanyuz_definer`) owns these
SECURITY DEFINER functions. A superuser migration creates it; otherwise it must exist
first (docs/deployment/database-roles.md) and the migrating role must be a member.
"""

from django.conf import settings
from django.db import migrations
from psycopg2 import sql

SHARED_READ_TABLES = [
    'catalog_question',
    'catalog_questionversion',
    'catalog_questionbank',
    'catalog_questionbank_questions',
    'exams_mockexam',
    'exams_mockexamquestion',
]

FUNCTIONS = """
CREATE OR REPLACE FUNCTION app_rls_bypass() RETURNS boolean
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ SELECT coalesce(current_setting('app.rls_bypass', true), '') = 'all' $$;

CREATE OR REPLACE FUNCTION app_question_shared(qid uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
    AS $$
    SELECT EXISTS (
               SELECT 1 FROM catalog_questionbank_questions bq
               JOIN catalog_questionbank b ON b.id = bq.questionbank_id
               WHERE bq.question_id = qid AND b.is_public)
        OR EXISTS (
               SELECT 1 FROM catalog_questionversion qv
               JOIN exams_mockexamquestion mq ON mq.question_version_id = qv.id
               JOIN exams_mockexam me ON me.id = mq.mock_exam_id
               WHERE qv.question_id = qid AND me.is_public)
    $$;

CREATE OR REPLACE FUNCTION app_question_visible(qid uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
    AS $$
    SELECT EXISTS (SELECT 1 FROM catalog_question q
                   WHERE q.id = qid AND q.organization_id = app_current_org_id())
        OR app_question_shared(qid)
    $$;

CREATE OR REPLACE FUNCTION app_search_question_versions(
        pattern text, max_rows integer, only_org uuid DEFAULT NULL)
    RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
    AS $$
    SELECT qv.id
      FROM catalog_questionversion qv
      JOIN catalog_question q ON q.id = qv.question_id
     WHERE upper(qv.content::text) LIKE upper(pattern)
       AND qv.is_quarantined IS NOT TRUE
       AND (only_org IS NULL OR q.organization_id = only_org)
       AND (app_rls_bypass()
            OR q.organization_id = app_current_org_id()
            OR app_question_shared(q.id))
     ORDER BY qv.created_at DESC, qv.id
     LIMIT max_rows
    $$;
"""

DEFINER_FUNCTIONS = [
    'app_question_shared(uuid)',
    'app_question_visible(uuid)',
    'app_search_question_versions(text, integer, uuid)',
]

POLICIES = [
    (
        'catalog_question',
        'catalog_question_org_select',
        'organization_id = app_current_org_id() OR app_question_shared(id)',
    ),
    ('catalog_questionversion', 'catalog_questionversion_org_select', 'app_question_visible(question_id)'),
]


def _select_policy_name(cur, table):
    cur.execute(
        "SELECT policyname FROM pg_policies WHERE schemaname = 'public' AND tablename = %s "
        "AND cmd = 'SELECT' AND policyname <> 'rls_bypass'",
        [table],
    )
    return [r[0] for r in cur.fetchall()]


def forward(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    role = getattr(settings, 'DB_DEFINER_ROLE', '') or 'yuzdanyuz_definer'
    r = sql.Identifier(role)
    with schema_editor.connection.cursor() as cur:
        cur.execute('SELECT rolsuper FROM pg_roles WHERE rolname = current_user')
        superuser = cur.fetchone()[0]
        cur.execute('SELECT rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = %s', [role])
        found = cur.fetchone()
        if found is None:
            if not superuser:
                raise RuntimeError(
                    f'Role {role} is missing. As a superuser run: CREATE ROLE {role} NOLOGIN '
                    f'BYPASSRLS; GRANT {role} TO <migrate role>; '
                    '(docs/deployment/database-roles.md)'
                )
            # cluster-wide object; parallel test databases may race to create it
            cur.execute(
                sql.SQL(
                    'DO $$ BEGIN CREATE ROLE {} NOLOGIN BYPASSRLS; '
                    'EXCEPTION WHEN duplicate_object THEN NULL; END $$'
                ).format(r)
            )
        elif not found[0] or found[1]:
            raise RuntimeError(f'Role {role} must be NOLOGIN BYPASSRLS')

        cur.execute(sql.SQL('GRANT USAGE, CREATE ON SCHEMA public TO {}').format(r))
        for table in SHARED_READ_TABLES:
            cur.execute(sql.SQL('GRANT SELECT ON {} TO {}').format(sql.Identifier(table), r))
        cur.execute(FUNCTIONS)
        for fn in DEFINER_FUNCTIONS:
            cur.execute(sql.SQL('ALTER FUNCTION ' + fn + ' OWNER TO {}').format(r))

        for table, name, clause in POLICIES:
            t = sql.Identifier(table)
            for old in _select_policy_name(cur, table):
                cur.execute(sql.SQL('DROP POLICY {} ON {}').format(sql.Identifier(old), t))
            cur.execute(
                sql.SQL('CREATE POLICY {} ON {} FOR SELECT USING (' + clause + ')').format(
                    sql.Identifier(name), t
                )
            )


class Migration(migrations.Migration):
    dependencies = [('ops', '0002_rls_force_and_bypass')]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]

"""E01: question search uses a trigram GIN index instead of a sequential scan.

`content__icontains` compiles to `UPPER(content::text) LIKE UPPER('%q%')`; a leading
wildcard can only be served by a pg_trgm index on that exact expression.
"""

import pytest
from django.db import connection

from apps.catalog.models import QuestionVersion
from apps.engagement.search_service import search_questions
from core.tenant import unscoped_context

INDEX_NAME = 'catalog_qv_content_trgm'


@pytest.mark.django_db
def test_trigram_index_exists():
    with connection.cursor() as cur:
        cur.execute(
            'SELECT indexdef FROM pg_indexes WHERE indexname = %s',
            [INDEX_NAME],
        )
        row = cur.fetchone()
    assert row, f'{INDEX_NAME} is missing'
    assert 'gin_trgm_ops' in row[0]


@pytest.mark.django_db
def test_search_query_plan_uses_the_index(org, subject):
    with unscoped_context():
        qs = QuestionVersion.objects.filter(content__icontains='parabola')
        sql, params = qs.query.sql_with_params()
    with connection.cursor() as cur:
        # Search runs as the owner of app_search_question_versions (ops.0003), a BYPASSRLS
        # role: under RLS the planner may not use this index for a LIKE (security barrier).
        cur.execute(
            "SELECT pg_get_userbyid(proowner) FROM pg_proc "
            "WHERE proname = 'app_search_question_versions'"
        )
        cur.execute(f'SET LOCAL ROLE {cur.fetchone()[0]}')
        # small test tables are always cheaper to scan; force the planner to show
        # whether the index can serve this predicate at all
        cur.execute('SET LOCAL enable_seqscan = off')
        cur.execute('EXPLAIN ' + sql, params)
        plan = '\n'.join(r[0] for r in cur.fetchall())
        cur.execute('RESET ROLE')
    assert INDEX_NAME in plan, plan


@pytest.mark.django_db
def test_search_still_matches_substrings(org, subject):
    from tests.integration.test_exam_api import _make_qv

    qv = _make_qv(org, subject)
    with unscoped_context():
        QuestionVersion.objects.filter(pk=qv.pk).update(
            content={'text': 'Find the vertex of the Parabola y = x^2'}
        )
        results = search_questions('parabola')
    assert [r['version_id'] for r in results] == [str(qv.id)]

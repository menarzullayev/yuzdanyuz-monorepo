"""
Task 9 — Search service.

PostgreSQL search over QuestionVersion.content (JSONB icontains). A Meilisearch
toggle used to sit here, but it was never implemented (it raised
NotImplementedError) and the meilisearch package was removed on 2026-11-11.
A real search engine, if needed, plugs in behind search_questions().

API:
  - search_questions(query, *, org=None, limit=20) → [{question_id, version_id, subject, snippet, score}, ...]
"""

import logging

logger = logging.getLogger(__name__)


def search_questions(query: str, *, org=None, limit: int = 20) -> list[dict]:
    """PostgreSQL ICONTAINS via QuestionVersion.content JSONB."""
    query = (query or '').strip()
    if not query or len(query) < 2:
        return []

    return _search_postgres(query, org=org, limit=limit)


def _like_pattern(query: str) -> str:
    """'%query%' with LIKE wildcards in the query escaped (as Django's icontains does)."""
    escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


def _search_postgres(query: str, *, org=None, limit: int = 20) -> list[dict]:
    """Substring search over the whole content JSON (pg_trgm index, E01).

    The match runs in app_search_question_versions (ops.0003): a SECURITY DEFINER function
    whose owner bypasses RLS, because a LIKE under RLS cannot use the trigram index. It
    applies the same visibility rule as the policies (own org, shared content, or the
    unscoped bypass); the rows are then loaded normally, under RLS.
    """
    from django.db import connection

    from apps.catalog.models import QuestionVersion
    from core.tenant import own_org_context

    # an explicit org is the caller's scope when it has no tenant context of its own (tasks);
    # inside a request the request's org stays in force (core.tenant.own_org_context)
    with own_org_context(org):
        with connection.cursor() as cur:
            cur.execute(
                'SELECT app_search_question_versions(%s, %s, %s)',
                [_like_pattern(query), limit, str(org.pk) if org is not None else None],
            )
            ids = [row[0] for row in cur.fetchall()]
        if not ids:
            return []
        by_id = {
            qv.id: qv
            for qv in QuestionVersion.objects.select_related('question__subject').filter(pk__in=ids)
        }

    results = []
    for qv in (by_id[i] for i in ids if i in by_id):
        text = (qv.content or {}).get('text', '') if isinstance(qv.content, dict) else ''
        snippet = text[:200]
        results.append(
            {
                'question_id': str(qv.question_id),
                'version_id': str(qv.id),
                'subject': qv.question.subject.name if qv.question.subject else None,
                'snippet': snippet,
                'score': 1.0,  # No real ranking
            }
        )
    return results

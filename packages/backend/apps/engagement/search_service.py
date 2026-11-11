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


def _search_postgres(query: str, *, org=None, limit: int = 20) -> list[dict]:
    """JSONField'da text qidiruv. Slow lekin small dataset uchun ok."""
    from apps.catalog.models import QuestionVersion

    qs = QuestionVersion.objects.select_related('question__subject')
    # Skip quarantined questions
    qs = qs.exclude(is_quarantined=True)

    # JSONField text search via cast (PG extension yoki contains)
    # Note: bu PostgreSQL'ga moslashtirilgan
    qs = qs.filter(content__icontains=query)
    if org is not None:
        qs = qs.filter(question__organization=org)

    results = []
    for qv in qs[:limit]:
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

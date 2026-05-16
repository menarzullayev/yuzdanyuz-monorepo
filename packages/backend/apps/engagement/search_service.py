"""
Task 9 — Search service (Meilisearch stub).

Stub mode (default): PostgreSQL FULL TEXT search fallback (Question.content
JSONB icontains).
Production: Meilisearch HTTP client (MEILISEARCH_URL).

API:
  - search_questions(query, *, org=None, limit=20) → [{id, title, snippet, score}, ...]
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def is_real_mode() -> bool:
    return bool(
        getattr(settings, 'MEILISEARCH_URL', '') and getattr(settings, 'MEILISEARCH_ENABLED', False)
    )


def search_questions(query: str, *, org=None, limit: int = 20) -> list[dict]:
    """
    Stub: PostgreSQL ICONTAINS via QuestionVersion.content JSONB.
    Production: Meilisearch GET /indexes/questions/search?q=...
    """
    query = (query or '').strip()
    if not query or len(query) < 2:
        return []

    if is_real_mode():
        return _search_meilisearch(query, limit=limit)

    return _search_postgres_fallback(query, org=org, limit=limit)


def _search_postgres_fallback(query: str, *, org=None, limit: int = 20) -> list[dict]:
    """
    Stub: JSONField'da text qidiruv. Slow lekin small dataset uchun ok.
    Production: Meilisearch index'ida millisekunda.
    """
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
                'score': 1.0,  # Stub: no real ranking
            }
        )
    return results


def _search_meilisearch(query: str, limit: int = 20) -> list[dict]:
    """Production placeholder."""
    # TODO production:
    # from meilisearch import Client
    # client = Client(settings.MEILISEARCH_URL, api_key=settings.MEILISEARCH_API_KEY)
    # res = client.index('questions').search(query, {'limit': limit})
    # return [normalize hit for hit in res['hits']]
    raise NotImplementedError('Real Meilisearch integratsiyasi kelajakda')

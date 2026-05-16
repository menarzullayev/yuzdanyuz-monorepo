"""ISSUE-X04 — CDN cache middleware unit tests.

Anonymous GETs on cacheable paths get `Cache-Control: public, max-age=N, s-maxage=N`.
Authenticated requests, non-GET, and unknown paths get no Cache-Control header.
"""

from unittest.mock import MagicMock

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from core.middleware.cdn_cache import CACHEABLE_PATTERNS, CDNCacheMiddleware


@pytest.fixture
def factory():
    return RequestFactory()


def _anon(request):
    """Attach an anonymous user to a RequestFactory request."""
    user = MagicMock()
    user.is_authenticated = False
    request.user = user
    return request


def _auth(request):
    """Attach an authenticated user to a RequestFactory request."""
    user = MagicMock()
    user.is_authenticated = True
    request.user = user
    return request


def _middleware():
    """Build middleware with a stub get_response returning empty 200."""
    return CDNCacheMiddleware(lambda req: HttpResponse('ok'))


def test_anon_get_sitemap_gets_cache_control(factory):
    """Anonymous GET /sitemap.xml → public, max-age=3600."""
    request = _anon(factory.get('/sitemap.xml'))
    response = _middleware()(request)
    assert response['Cache-Control'] == 'public, max-age=3600, s-maxage=3600'


def test_authenticated_get_sitemap_no_cache_control(factory):
    """Authenticated GET /sitemap.xml → no Cache-Control header."""
    request = _auth(factory.get('/sitemap.xml'))
    response = _middleware()(request)
    assert 'Cache-Control' not in response


def test_post_request_no_cache_control(factory):
    """POST never gets Cache-Control even on cacheable paths."""
    request = _anon(factory.post('/sitemap.xml'))
    response = _middleware()(request)
    assert 'Cache-Control' not in response


def test_unknown_path_no_cache_control(factory):
    """Non-cacheable path → no Cache-Control header."""
    request = _anon(factory.get('/api/v1/some/private/'))
    response = _middleware()(request)
    assert 'Cache-Control' not in response


def test_anon_get_leaderboard_global_60s(factory):
    """Anonymous GET /api/v1/leaderboard/global/ → 60s max-age."""
    request = _anon(factory.get('/api/v1/leaderboard/global/'))
    response = _middleware()(request)
    assert response['Cache-Control'] == 'public, max-age=60, s-maxage=60'


def test_anon_get_robots_txt_1day(factory):
    """Anonymous GET /robots.txt → 86400s (1 day) max-age."""
    request = _anon(factory.get('/robots.txt'))
    response = _middleware()(request)
    assert response['Cache-Control'] == 'public, max-age=86400, s-maxage=86400'


def test_prefix_match_handles_subpath(factory):
    """Region leaderboard subpath like /api/v1/leaderboard/region/uz-TK/ matches."""
    request = _anon(factory.get('/api/v1/leaderboard/region/uz-TK/'))
    response = _middleware()(request)
    assert response['Cache-Control'] == 'public, max-age=60, s-maxage=60'


def test_cacheable_patterns_constant_intact():
    """Sanity: configured patterns include all expected public endpoints."""
    assert '/sitemap.xml' in CACHEABLE_PATTERNS
    assert '/robots.txt' in CACHEABLE_PATTERNS
    assert '/api/v1/leaderboard/global/' in CACHEABLE_PATTERNS
    assert '/api/v1/leaderboard/region/' in CACHEABLE_PATTERNS

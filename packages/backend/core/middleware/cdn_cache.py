"""ISSUE-X04 — CDN/edge cache headers for read-only public endpoints.

Cloudflare/edge reverse-proxies use Cache-Control to cache responses at the edge,
reducing Django app server load for high-traffic anonymous reads.
"""

CACHEABLE_PATTERNS = {
    '/sitemap.xml': 3600,  # 1 hour
    '/robots.txt': 86400,  # 1 day
    '/api/v1/leaderboard/global/': 60,  # 60 seconds — anonymous, expensive to compute
    '/api/v1/leaderboard/region/': 60,
}


class CDNCacheMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.method != 'GET' or request.user.is_authenticated:
            return response
        for prefix, max_age in CACHEABLE_PATTERNS.items():
            if request.path.startswith(prefix):
                response['Cache-Control'] = f'public, max-age={max_age}, s-maxage={max_age}'
                break
        return response

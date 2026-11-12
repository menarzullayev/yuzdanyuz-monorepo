"""C14 — Content-Security-Policy on every Django response, nginx and Next.js.

Enforced: API/JSON responses (strict `default-src 'none'`) and the Django admin
(nonce-based scripts). Report-only: the remaining HTML pages (index, Swagger/Redoc,
allauth) that still load htmx/Alpine/Swagger UI from CDNs; for those `frame-ancestors`,
`object-src` and `base-uri` are still enforced.
"""

import re
from pathlib import Path

import pytest
from django.urls import reverse

REPO = Path(__file__).resolve().parents[4]
ENFORCED = 'Content-Security-Policy'
REPORT_ONLY = 'Content-Security-Policy-Report-Only'


def _directives(header: str) -> dict[str, str]:
    out = {}
    for part in header.split(';'):
        part = part.strip()
        if part:
            name, _, value = part.partition(' ')
            out[name] = value.strip()
    return out


def _inline_scripts_without_nonce(html: str, nonce: str) -> list[str]:
    bad = []
    for tag in re.findall(r'<script\b[^>]*>', html):
        if 'src=' in tag:
            continue
        if f'nonce="{nonce}"' not in tag:
            bad.append(tag)
    return bad


@pytest.mark.django_db
class TestApi:
    def test_json_api_response_has_strict_enforced_csp(self, client):
        resp = client.get('/api/v1/exams/')
        csp = _directives(resp[ENFORCED])
        assert csp['default-src'] == "'none'"
        assert csp['frame-ancestors'] == "'none'"

    def test_health_endpoint_has_csp(self, client):
        resp = client.get('/health/')
        assert _directives(resp[ENFORCED])['frame-ancestors'] == "'none'"


@pytest.mark.django_db
class TestAdmin:
    @pytest.fixture
    def admin_client(self, client, superuser):
        client.force_login(superuser)
        return client

    @pytest.mark.parametrize(
        'url',
        [
            '/admin/',
            '/admin/login/',
            '/admin/accounts/customuser/',
            '/admin/catalog/aiproviderconfig/add/',
        ],
    )
    def test_admin_pages_enforce_a_nonce_policy(self, admin_client, url):
        resp = admin_client.get(url)
        assert resp.status_code == 200, url
        assert ENFORCED in resp, url
        csp = _directives(resp[ENFORCED])
        nonce = re.search(r"'nonce-([^']+)'", csp['script-src']).group(1)
        assert "'unsafe-inline'" not in csp['script-src']
        assert "'unsafe-eval'" not in csp['script-src']
        assert csp['frame-ancestors'] == "'none'"
        assert csp['object-src'] == "'none'"
        assert csp['base-uri'] in ("'self'", "'none'")
        assert _inline_scripts_without_nonce(resp.content.decode(), nonce) == []

    def test_nonce_changes_per_request(self, admin_client):
        a = admin_client.get('/admin/')[ENFORCED]
        b = admin_client.get('/admin/')[ENFORCED]
        assert a != b


@pytest.mark.django_db
class TestOtherHtml:
    def test_index_is_report_only_but_still_blocks_framing(self, client):
        resp = client.get(reverse('home'))
        enforced = _directives(resp[ENFORCED])
        assert enforced['frame-ancestors'] == "'none'"
        assert enforced['object-src'] == "'none'"
        report = _directives(resp[REPORT_ONLY])
        nonce = re.search(r"'nonce-([^']+)'", report['script-src']).group(1)
        assert _inline_scripts_without_nonce(resp.content.decode(), nonce) == []

    def test_swagger_is_report_only(self, client, settings):
        resp = client.get(reverse('swagger-ui') if _has('swagger-ui') else '/api/schema/swagger/')
        if resp.status_code == 404:
            pytest.skip('swagger disabled in this settings module')
        assert REPORT_ONLY in resp
        assert _directives(resp[ENFORCED])['frame-ancestors'] == "'none'"


def _has(name):
    from django.urls import NoReverseMatch

    try:
        reverse(name)
        return True
    except NoReverseMatch:
        return False


class TestEdgeConfigs:
    def test_nginx_sets_csp_on_its_own_responses(self):
        conf = (REPO / 'docker/nginx/default.conf').read_text()
        assert 'Content-Security-Policy' in conf
        assert "frame-ancestors 'none'" in conf
        # static/media: files are served by nginx itself, so the header must be there
        snippet = (REPO / 'docker/nginx/security-headers.conf').read_text()
        assert "default-src 'none'" in snippet or "frame-ancestors 'none'" in snippet
        for loc in ('location /static/', 'location /media/'):
            block = conf.split(loc, 1)[1].split('}', 1)[0]
            assert 'security-headers.conf' in block, loc

    def test_ingress_sets_csp_fallback(self):
        values = (REPO / 'charts/yuzdanyuz/values.yaml').read_text()
        assert "frame-ancestors 'none'" in values

    def test_nextjs_sends_csp(self):
        text = (REPO / 'packages/frontend-web/next.config.ts').read_text()
        assert 'Content-Security-Policy' in text
        assert "frame-ancestors 'none'" in text
        assert "object-src 'none'" in text

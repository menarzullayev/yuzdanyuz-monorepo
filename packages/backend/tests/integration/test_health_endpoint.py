"""
Task 10 — /health/ + /health/ready/ + /metrics endpoint integration tests.

Liveness/readiness probes are critical for K8s rollout safety; if these break,
the cluster will get stuck in CrashLoopBackOff or refuse traffic to healthy
pods. Hence: explicit tests, not just smoke checks.
"""

import pytest
from django.test import Client


@pytest.mark.integration
class TestHealthEndpoints:
    def test_liveness_returns_200(self, db):
        client = Client()
        resp = client.get('/health/')
        assert resp.status_code == 200
        assert resp.json() == {'status': 'ok'}

    def test_liveness_does_not_require_auth(self, db):
        # K8s probes hit this without cookies/headers — must always succeed
        client = Client()
        resp = client.get('/health/')
        assert resp.status_code == 200

    def test_readiness_returns_200_when_db_up(self, db):
        client = Client()
        resp = client.get('/health/ready/')
        # In tests, DB is always up; redis may or may not be — accept either
        assert resp.status_code in (200, 503)
        data = resp.json()
        assert 'checks' in data
        assert 'database' in data['checks']
        assert data['checks']['database'] is True

    def test_readiness_includes_redis_check(self, db):
        client = Client()
        resp = client.get('/health/ready/')
        assert 'redis' in resp.json()['checks']


@pytest.mark.integration
class TestMetricsEndpoint:
    def test_metrics_returns_200(self, db):
        client = Client()
        resp = client.get('/metrics')
        assert resp.status_code == 200
        # django-prometheus emits text/plain Prometheus exposition format
        assert b'# HELP' in resp.content
        assert b'# TYPE' in resp.content

    def test_metrics_contains_django_request_counter(self, db):
        client = Client()
        # First, generate at least one request so the counter exists
        client.get('/health/')
        resp = client.get('/metrics')
        # django-prometheus exposes django_http_requests_total_by_method_total
        assert b'django_http_requests' in resp.content

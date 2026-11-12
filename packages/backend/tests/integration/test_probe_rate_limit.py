"""Probes and the Prometheus scrape are never rate-limited.

Found in the 2026-11-12 load test: kubelet probes, the container HEALTHCHECK and the
/metrics scrape all come from one IP, fell into the anonymous 60/min "api" tier and
got 429 — a pod answering 429 to its readiness probe is taken out of the Service,
and a 429 scrape makes BackendDown fire on a healthy pod.
"""

import pytest
from django.test import Client


@pytest.mark.integration
@pytest.mark.parametrize('path', ['/health/', '/health/ready/', '/metrics'])
def test_probe_paths_never_get_429(db, path):
    client = Client(REMOTE_ADDR='10.42.0.1')
    codes = {client.get(path).status_code for _ in range(150)}
    assert 429 not in codes, codes


@pytest.mark.integration
def test_other_anonymous_paths_are_still_limited(db):
    client = Client(REMOTE_ADDR='10.42.0.2')
    codes = [client.get('/ping/').status_code for _ in range(70)]
    assert 429 in codes

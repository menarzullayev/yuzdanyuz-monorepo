"""G03/G04 — every metric the dashboards and alert rules query is actually emitted.

The Celery dashboard used to query celery_task_runtime_seconds_bucket and the
Django one django_db_execute_seconds_bucket, which nothing produced, so panels
were empty and alerts could never fire. This test resolves every metric name in
charts/yuzdanyuz/files/ against:

- what the backend exposes on /metrics (django-prometheus, yz_* counters),
- what celery-exporter 0.12.2 serves (EXPORTER_METRICS, from its exporter.py),
- exporters the platform runs (kube-state-metrics, cAdvisor, postgres/redis exporters).
"""

import contextlib
import json
import re
from pathlib import Path

import prometheus_client
import pytest
import yaml
from django.test import Client

REPO = Path(__file__).resolve().parents[4]
FILES = REPO / 'charts' / 'yuzdanyuz' / 'files'

# danihodovic/celery-exporter 0.12.2, src/exporter.py (Counter/Gauge/Histogram names).
_EXPORTER_BASE = {
    'celery_task_sent_total',
    'celery_task_received_total',
    'celery_task_started_total',
    'celery_task_succeeded_total',
    'celery_task_failed_total',
    'celery_task_rejected_total',
    'celery_task_revoked_total',
    'celery_task_retried_total',
    'celery_worker_up',
    'celery_worker_tasks_active',
    'celery_queue_length',
    'celery_active_consumer_count',
    'celery_active_worker_count',
    'celery_active_process_count',
}
_EXPORTER_HISTOGRAMS = {'celery_task_runtime', 'celery_task_queue_wait_time'}
EXPORTER_METRICS = _EXPORTER_BASE | {
    f'{h}{s}' for h in _EXPORTER_HISTOGRAMS for s in ('_bucket', '_sum', '_count')
}

# Emitted by components outside this repo; the prefix decides the owner.
EXTERNAL_PREFIXES = ('kube_', 'container_', 'pg_', 'redis_')
PROMQL_BUILTINS = {'up'}

WORD_RE = re.compile(r'\b[a-z][a-z0-9_]*_[a-z0-9_]+\b')
PROMQL_WORDS = {'histogram_quantile', 'label_values', 'clamp_min'}


def _exprs():
    rules = yaml.safe_load((FILES / 'prometheus-rules.yml').read_text(encoding='utf-8'))
    for group in rules['groups']:
        for rule in group['rules']:
            yield f'rule {rule["alert"]}', rule['expr']
    for path in sorted((FILES / 'dashboards').glob('*.json')):
        dash = json.loads(path.read_text(encoding='utf-8'))
        for panel in dash['panels']:
            for target in panel.get('targets', []):
                yield f'{path.name}: {panel["title"]}', target['expr']


def _metric_names(expr: str) -> set[str]:
    # Drop label matchers ({...}) and strings so label names/values are not metrics.
    bare = re.sub(r'\{[^}]*\}', '{}', expr)
    bare = re.sub(r'"[^"]*"', '""', bare)
    names = set(WORD_RE.findall(bare)) | ({'up'} if re.search(r'\bup\{', expr) else set())
    by_clause = set(re.findall(r'\b(?:by|without)\s*\(([^)]*)\)', bare))
    labels = {x.strip() for clause in by_clause for x in clause.split(',')}
    return {n for n in names if n not in labels and n not in PROMQL_WORDS}


def _exposed() -> set[str]:
    client = Client()
    client.get('/health/')  # middleware + DB metrics get samples
    text = client.get('/metrics').content.decode()
    names = set()
    for line in text.splitlines():
        if line.startswith('# TYPE '):
            _, _, name, kind = line.split(' ', 3)
            names.add(name)
            if kind == 'histogram':
                names |= {f'{name}_bucket', f'{name}_sum', f'{name}_count'}
            if kind == 'counter' and not name.endswith('_total'):
                names.add(f'{name}_total')
        elif line and not line.startswith('#'):
            names.add(re.split(r'[{ ]', line, maxsplit=1)[0])
    # Labelled metrics with no samples yet (yz_* counters bumped in workers) are not
    # printed on /metrics; the registry still proves the code defines them.
    import core.metrics  # noqa: F401

    names |= set(prometheus_client.REGISTRY._names_to_collectors)
    return names


def unresolved(exposed: set[str]) -> list[str]:
    missing = []
    for where, expr in _exprs():
        for name in _metric_names(expr):
            if name in PROMQL_BUILTINS or name.startswith(EXTERNAL_PREFIXES):
                continue
            if name.startswith('celery_'):
                if name not in EXPORTER_METRICS:
                    missing.append(f'{where}: {name} (not a celery-exporter metric)')
            elif name not in exposed:
                missing.append(f'{where}: {name} (not on /metrics)')
    return missing


@pytest.mark.integration
def test_every_queried_metric_is_emitted(db):
    assert unresolved(_exposed()) == []


@pytest.mark.integration
def test_old_dashboard_names_would_be_caught():
    assert _metric_names(
        'histogram_quantile(0.95, sum by (le, name) (rate(celery_task_runtime_seconds_bucket[5m])))'
    ) == {'celery_task_runtime_seconds_bucket'}
    assert 'celery_task_runtime_seconds_bucket' not in EXPORTER_METRICS
    assert _metric_names('sum by (queue) (celery_queue_length{queue="default"}) > 5') == {
        'celery_queue_length'
    }


@pytest.mark.integration
def test_db_metrics_are_emitted(db):
    # django_db_* only exist with the django_prometheus DB engine wrapper.
    exposed = _exposed()
    assert 'django_db_query_duration_seconds_bucket' in exposed
    assert 'django_db_new_connections_total' in exposed


@pytest.mark.unit
def test_dlq_counter_counts_every_failure():
    from apps.analytics import dlq
    from core.metrics import TASK_DLQ

    class Sender:
        name = 'tests.always_fails'

    before = TASK_DLQ.labels(task_name=Sender.name)._value.get()
    for _ in range(3):
        # The DB write after the counter needs a database this test does not have.
        with contextlib.suppress(Exception):
            dlq.record_failure(task_id='t', exception=ValueError('x'), sender=Sender())
    assert TASK_DLQ.labels(task_name=Sender.name)._value.get() - before == 3

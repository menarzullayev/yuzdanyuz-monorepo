# Monitoring — Sentry + Prometheus + OpenTelemetry

Uch qatlamli observability stack:

| Layer        | Purpose                          | Backend                         |
|--------------|----------------------------------|---------------------------------|
| **Sentry**   | Error tracking + performance     | sentry.io (managed) or self-host|
| **Prometheus**| Metrics + alerting               | kube-prometheus-stack           |
| **OpenTelemetry**| Distributed tracing + log corr | OTel Collector → Tempo + Loki   |

## 1. Sentry

Initialization: [`core/observability.py:init_sentry`](../../core/observability.py).

Environment variables (set in K8s ConfigMap or Secret):

| Variable                       | Default | Description                        |
|--------------------------------|---------|------------------------------------|
| `SENTRY_DSN`                   | —       | Required to enable                 |
| `SENTRY_ENVIRONMENT`           | `prod`  | Environment tag                    |
| `SENTRY_RELEASE`               | git SHA | Release version                    |
| `SENTRY_TRACES_SAMPLE_RATE`    | `0.1`   | Trace sampling (0.05 in prod)      |
| `SENTRY_PROFILES_SAMPLE_RATE`  | `0.0`   | Profiling (off by default)         |

Integrations: Django, Celery, Redis, Logging (INFO breadcrumbs, ERROR events).

PII masking: `send_default_pii=False` — request headers, cookies, body stripped.

## 2. Prometheus / django-prometheus

Endpoint: `GET /metrics` (no auth — restrict at NetworkPolicy/Ingress level).

Auto-instrumented metrics:
- `django_http_requests_total_by_method_total{method}`
- `django_http_responses_total_by_status_total{status}`
- `django_http_requests_latency_seconds_by_view_method_bucket{view, method, le}`
- `django_db_execute_seconds_bucket{...}`

ServiceMonitor (Prometheus Operator):
[`charts/yuzdanyuz/templates/servicemonitor.yaml`](../../../../charts/yuzdanyuz/templates/servicemonitor.yaml)

Standalone scrape config:
[`monitoring/prometheus/prometheus.yml`](../../../../monitoring/prometheus/prometheus.yml)

Alert rules:
[`monitoring/prometheus/alerts.yml`](../../../../monitoring/prometheus/alerts.yml)

Defined alerts:
- `HighRequestErrorRate` (5xx > 5% / 5min)
- `HighRequestLatencyP95` (>1.5s / 10min)
- `BackendDown` (up==0 / 2min)
- `CeleryQueueBacklog` (>1000 / 10min)
- `CeleryTaskFailureRate` (>10% / 10min)
- `PostgresHighConnections` (>85% / 5min)
- `PostgresReplicationLag` (>60s / 5min)
- `BackupTooOld` (>25h / 1h)
- `RedisDown`, `RedisHighMemory`

## 3. OpenTelemetry

Auto-instrumentation via `opentelemetry-instrument` wrapper in
[`docker/entrypoint.sh`](../../../../docker/entrypoint.sh) (opt-in: `OTEL_ENABLED=true`).

Instrumented:
- `opentelemetry-instrumentation-django` — HTTP server spans
- `opentelemetry-instrumentation-celery` — task spans
- `opentelemetry-instrumentation-psycopg2` — DB query spans
- `opentelemetry-instrumentation-redis` — Redis ops
- `opentelemetry-instrumentation-requests` — outbound HTTP

Environment:

| Variable                       | Default                       | Description           |
|--------------------------------|-------------------------------|-----------------------|
| `OTEL_ENABLED`                 | `false`                       | Master toggle         |
| `OTEL_SERVICE_NAME`            | `yuzdanyuz-backend`           | Service identifier    |
| `OTEL_EXPORTER_OTLP_ENDPOINT`  | `http://otel-collector:4317`  | OTel Collector        |
| `OTEL_TRACES_EXPORTER`         | `otlp`                        | otlp / console / none |
| `OTEL_METRICS_EXPORTER`        | `otlp`                        |                       |
| `OTEL_LOGS_EXPORTER`           | `none`                        | logs via JSON instead |

Collector config:
[`monitoring/otel/collector.yaml`](../../../../monitoring/otel/collector.yaml)

Pipelines:
- traces  → Tempo (Grafana)
- metrics → Prometheus (push)
- logs    → Loki (OTLP-HTTP)

## 4. JSON logging

Production logging — JSON-formatted to stdout (collected by k8s log driver →
Loki/CloudWatch/Datadog). Defined: [`core/observability.py:json_logging_dict`](../../core/observability.py).

Format: `{"time": "...", "level": "INFO", "name": "celery.worker", "module": "...", "message": "..."}`

## 5. Grafana dashboards

Pre-built JSON dashboards (import via Grafana UI):

- [`monitoring/grafana/dashboards/django-overview.json`](../../../../monitoring/grafana/dashboards/django-overview.json) — req rate, 5xx %, p50/p95/p99, top slow views, DB query duration
- [`monitoring/grafana/dashboards/celery-overview.json`](../../../../monitoring/grafana/dashboards/celery-overview.json) — tasks/s, failure rate, queue depth, task runtime p95

## Verification

```bash
# In a running pod:
curl -s http://localhost:8000/metrics | head -20
# → # HELP python_gc_objects_collected_total ...

# Check Sentry init logs
kubectl -n yuzdanyuz-prod logs -l app.kubernetes.io/component=web --tail=50 | grep -i sentry

# OpenTelemetry — verify traces flowing
kubectl -n monitoring port-forward svc/tempo 3200
# Open Grafana → Explore → Tempo → service=yuzdanyuz-backend
```

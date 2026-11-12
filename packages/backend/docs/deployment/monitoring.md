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

## 2. Prometheus, alerts and dashboards

### What the chart ships and what it expects

The chart does **not** install Prometheus, Alertmanager or Grafana. Install
[kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
once per cluster (release `prometheus` in namespace `monitoring`, so its selectors match
the `release: prometheus` label below). It also brings kube-state-metrics and cAdvisor,
which the saturation panels and the backup alerts use. The `monitoring.coreos.com` CRDs
must exist before this chart syncs.

| Chart object | Template | Toggle (values.yaml) |
|---|---|---|
| ServiceMonitor, `job=yuzdanyuz-backend` (web `/metrics`) | `templates/servicemonitor.yaml` | `serviceMonitor.enabled` |
| PodMonitor, `job=yuzdanyuz-celery-worker` (worker `:9540`) | `templates/servicemonitor.yaml` | `serviceMonitor.enabled` |
| celery-exporter Deployment + ServiceMonitor, `job=yuzdanyuz-celery-exporter` | `templates/celery-exporter.yaml` | `celeryExporter.enabled` |
| PrometheusRule (alerts) from `files/prometheus-rules.yml` | `templates/prometheusrule.yaml` | `prometheusRule.enabled` |
| Grafana dashboards ConfigMap (`grafana_dashboard: "1"`) from `files/dashboards/` | `templates/grafana-dashboards.yaml` | `grafanaDashboards.enabled` |
| AlertmanagerConfig, Telegram receiver | `templates/alertmanagerconfig.yaml` | `alerting.telegram.enabled` |

kube-prometheus-stack values the objects above rely on:

```yaml
prometheus:
  prometheusSpec:
    ruleSelector: {matchLabels: {release: prometheus}}
    serviceMonitorSelector: {matchLabels: {release: prometheus}}
    podMonitorSelector: {matchLabels: {release: prometheus}}
    ruleNamespaceSelector: {}
    serviceMonitorNamespaceSelector: {}
    podMonitorNamespaceSelector: {}
alertmanager:
  alertmanagerSpec:
    alertmanagerConfigSelector: {matchLabels: {alertmanagerConfig: yuzdanyuz}}
    alertmanagerConfigNamespaceSelector: {}
grafana:
  sidecar:
    dashboards:
      enabled: true
      label: grafana_dashboard
      searchNamespace: ALL
      folderAnnotation: grafana_folder
```

### Metric sources

- **Web** (`/metrics`, django-prometheus): `django_http_*`, `django_db_*` (the DB engine
  is `django_prometheus.db.backends.postgresql`), business counters `yz_*` (`core/metrics.py`).
- **Celery workers** (`:9540`, `core.celery.start_worker_metrics_server`): `yz_*` counters
  bumped inside tasks (`yz_exams_submitted_total`, `yz_task_dlq_total`, AI calls).
- **celery-exporter** (`:9808`): `celery_task_{sent,received,started,succeeded,failed,retried}_total`,
  `celery_task_runtime_bucket`, `celery_task_queue_wait_time_bucket`,
  `celery_queue_length{queue_name}`, `celery_worker_up`. It reads Celery task events, which
  `CELERY_WORKER_SEND_TASK_EVENTS` / `CELERY_TASK_SEND_SENT_EVENT` (`core/settings/base.py`) turn on.

gunicorn and Celery prefork run several processes, so `docker/entrypoint.sh` sets
`PROMETHEUS_MULTIPROC_DIR` and each scrape sums all of them (before, a scrape saw one
random gunicorn worker).

`tests/integration/test_monitoring_metrics.py` fails CI when a dashboard or an alert
queries a metric that nothing emits.

### Alerts

Source: [`charts/yuzdanyuz/files/prometheus-rules.yml`](../../../../charts/yuzdanyuz/files/prometheus-rules.yml).
CI runs `promtool check rules` and `promtool test rules monitoring/prometheus/tests/rules-test.yml`.

| Alert | Fires when | Severity |
|---|---|---|
| `HighRequestErrorRate` | 5xx > 5% for 5m | critical |
| `HighRequestLatencyP95` | p95 > 1.5s for 10m | warning |
| `BackendDown` / `BackendNotScraped` | web target down 2m / no target at all 10m | critical |
| `CeleryExporterDown` | exporter not scraped 5m | warning |
| `CeleryWorkerDown` | a worker stopped heartbeating 5m | critical |
| `CeleryCriticalQueueBacklog` | `critical` queue > 50 for 2m | critical |
| `CeleryRealtimeQueueBacklog` / `CeleryAIQueueBacklog` | > 200 for 5m / 15m | warning |
| `CeleryDefaultQueueBacklog` / `CeleryBatchQueueBacklog` | > 1000 for 10m / > 5000 for 30m | warning |
| `CeleryTaskFailureRate` | failed / received > 10% for 10m | warning |
| `CeleryCriticalTaskFailed` | any `critical` queue task failed | critical |
| `TasksDeadLettered` | `yz_task_dlq_total` grew in 15m | warning |
| `CeleryBeatMissedRuns` | `ops.run_health_probe` (every 5m) not received for 20m | critical |
| `BackupTooOld` / `BackupJobFailed` | no successful backup CronJob in 25h / a backup Job failed | critical |
| `Postgres*`, `Redis*` | need postgres_exporter / redis_exporter from the DB / Redis release | — |

### Alert delivery (Telegram)

The bot token never goes into git or values:

```bash
kubectl -n yuzdanyuz-prod create secret generic yuzdanyuz-alertmanager-telegram \
  --from-literal=bot-token='<token from @BotFather>'
```

Then per environment (values-prod.yaml or `--set`):

```yaml
alerting:
  telegram:
    enabled: true
    chatId: "-1001234567890"   # ops group or channel; the bot must be a member
```

Critical alerts wait 10s and repeat hourly; the rest group for 30s and repeat every 4h.
Send a test alert once after setup:

```bash
kubectl -n monitoring port-forward svc/prometheus-kube-prometheus-alertmanager 9093
amtool alert add test severity=critical namespace=yuzdanyuz-prod --alertmanager.url=http://localhost:9093
```

### External uptime

Cloudflare health checks probe `https://yuzdanyuz.uz/health/ready/` and
`https://api.yuzdanyuz.uz/health/ready/` every minute from three regions
([`terraform/cloudflare/uptime.tf`](../../../../terraform/cloudflare/uptime.tf); Pro plan or
higher; email notification via `uptime_alert_emails`). They alert even when the cluster or
the in-cluster Alertmanager is down.

### Local stack

`docker compose --profile monitoring up -d` adds celery-exporter, Prometheus (:9090),
Alertmanager (:9093) and Grafana (:3001) with the same rules and dashboards
([`monitoring/prometheus/prometheus.yml`](../../../../monitoring/prometheus/prometheus.yml),
[`monitoring/alertmanager/alertmanager.yml`](../../../../monitoring/alertmanager/alertmanager.yml));
put the bot token and the chat id in the gitignored `monitoring/alertmanager/telegram-*` files.

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

Format: `{"time": "...", "level": "INFO", "name": "celery.worker", "module": "...", "message": "...", "request_id": "..."}`

Every HTTP request writes one access line on the `yuzdanyuz.access` logger
([`core/middleware/request_id.py`](../../core/middleware/request_id.py)):
`request_id`, `method`, `path` (no query string), `status`, `duration_ms`, `user_id`.
The id comes from a well-formed incoming `X-Request-ID` or is generated; it is returned in
the `X-Request-ID` response header, added to every log line of the request and set as the
Sentry `request_id` tag (the Sentry user is the user id only). Successful `/health/` and
`/metrics` hits are not logged; `ACCESS_LOG_LEVEL=WARNING` keeps only failed requests.

Secrets: the formatters redact Telegram bot tokens and `token=`/`key=`/`secret=`/... query
parameters, httpx/httpcore/urllib3 log at WARNING only, and Sentry breadcrumbs and events
go through the same scrubber (`core/observability.py`). `/health/ready/?deep=1` (calls the
Telegram, Anthropic and payment APIs) needs `X-Health-Token: $HEALTH_DEEP_TOKEN` or a staff
session; everyone else gets 403.

## 5. Grafana dashboards

Shipped by the chart (sidecar import, folder `yuzdanyuz`) and mounted by the local stack:

- [`charts/yuzdanyuz/files/dashboards/django-overview.json`](../../../../charts/yuzdanyuz/files/dashboards/django-overview.json):
  request rate, 5xx ratio, p50/p95/p99, slow views, DB query p95, web pod CPU and memory
  against the limit, HPA replicas, exams submitted.
- [`charts/yuzdanyuz/files/dashboards/celery-overview.json`](../../../../charts/yuzdanyuz/files/dashboards/celery-overview.json):
  tasks/s, failure ratio, queue depth and queue wait p95 per queue, runtime p95, workers
  up, dead-lettered tasks, worker CPU.

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

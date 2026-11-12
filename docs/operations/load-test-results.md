# Load test results

How to run: [`load-testing.md`](load-testing.md). Script: `loadtest/k6/core-flows.js`.

## 2026-11-12: peak x2 (400 VUs), local compose stack

**Machine:** Intel Core i7-13700F, 32 GB RAM, Windows 11; Docker Desktop 29.8.2 VM with
6 CPUs and 9.7 GB. k6 (grafana/k6:1.8.2), Postgres 16, Redis 7 and the Celery worker ran
on the same VM, so the web containers competed with them for the 6 CPUs.

**Stack:** backend image built from this branch (based on `402e821`), `core.settings.prod`,
gunicorn 4 workers x 2 threads, every web container limited to 1 CPU and 1 GiB (the
chart's per-pod limit). Postgres on tmpfs (`max_connections=200`), one Celery worker
(concurrency 4). Data: 400 students, 40 open mocks x 10 questions.

**Load:** ramp 0→200 VUs (1m), hold 200 (2m), ramp to 400 (1m), hold 400 (3m), ramp down
(30s). Each VU: login, list mocks, start, 10 answers with 1–3 s reading time, submit,
2–5 s pause.

### Final run: 3 web pods (default `web.replicas`), rate-limiter fix applied

| Metric | Result | Threshold | |
|---|---|---|---|
| Requests | 61,396 (129 req/s) | | |
| Exams completed | 4,692 (flow p50 23.9 s, p95 31.6 s) | | |
| Error rate (`http_req_failed`) | **0.25%** | < 1% | pass |
| Checks | **99.75%** | > 99% | pass |
| All requests p50 / p90 / p95 / p99 / max | 114 / 995 / **1405** / 2161 / 3475 ms | p95 < 800, p99 < 2000 | **fail** |
| login p50 / p95 | 225 / 1755 ms | p95 < 1500 | fail |
| list mocks p50 / p95 | 135 / 1458 ms | p95 < 500 | fail |
| start attempt p50 / p95 | 110 / 1421 ms | p95 < 1000 | fail |
| answer p50 / p95 | 111 / 1399 ms | p95 < 500 | fail |
| submit p50 / p95 | 104 / 1370 ms | p95 < 1000 | fail |

Resources (docker stats every 15 s): web pods 67–71% CPU on average and 100–106% at the
400 VU plateau, 655 MiB max per pod; Postgres 72% average, 128% peak; worker 30%.
The 0.25% errors are 155 `connection reset by peer` on POSTs while the pods were at their
CPU limit (gunicorn worker restarts), no 5xx bodies and no 429.

**Verdict:** at 2x the assumed peak the stack stays correct (no lost submits, error rate
within budget), but 3 pods at 1 CPU are CPU-bound and the latency targets fail. Median
latency stays near 110 ms; the tail comes from queueing at the CPU limit. Production runs
`web.replicas: 4` with HPA at 60% CPU (values-prod.yaml), which should add pods during
such a ramp, but HPA reaction time (≈1–2 min) is longer than this ramp. Before launch:
re-run this test on staging with HPA enabled, and either raise `web.hpa.minReplicas` in
prod to 6 for exam windows or schedule a pre-scale before announced mock exams.

### Earlier runs the same day (kept for comparison)

| Run | Req/s | Errors | p50 / p95 / p99 ms | Finding |
|---|---|---|---|---|
| 1 web pod | 58 | 12.7% | 2741 / 8545 / 18503 | one pod saturates at ~58 req/s; gunicorn timeouts reset connections |
| 3 pods, before fix | 107 | 18.3% | 96 / 2021 / 4005 | errors were 429s from the rate limiter, see below |

### Bugs found and fixed in this branch

1. **Rate-limit window never reset for active users** (`core/utils/rate_limiter.py`).
   `check()` refreshed the counter's TTL on every request, so anyone sending at least one
   request a minute kept one ever-growing counter. A student was blocked (429, then 1, 5,
   30, 120 minutes) after 60 POSTs in a session regardless of the real rate: answer 61 of a
   90-question DTM mock. Fixed with `EXPIRE … NX` (fixed window; needs Redis ≥ 7.0);
   `tests/unit/test_rate_limiter_window.py`.
2. **Probes and the Prometheus scrape were rate-limited** (`core/middleware/rate_limit.py`).
   The container HEALTHCHECK alone pushed `/health/`, `/health/ready/` and `/metrics` into
   429 after a minute; in Kubernetes that marks healthy pods unready and fires
   `BackendDown`. These paths are now exempt; `tests/integration/test_probe_rate_limit.py`.

### Observability check during the run

celery-exporter reported `celery_task_succeeded_total{name="exams.finalize_attempt_score"}`
and `celery_queue_length` for all six queues (0 backlog), `celery_worker_up=1`; the worker
metrics server reported `yz_exams_submitted_total` 1823 in the first run, matching the
submits; every response carried `X-Request-ID`.

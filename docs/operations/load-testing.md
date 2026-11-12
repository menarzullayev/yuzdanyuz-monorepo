# Load testing (k6)

Core exam flow under load: password login → list mocks → start attempt → answer every
question → submit. Script: [`loadtest/k6/core-flows.js`](../../loadtest/k6/core-flows.js).
Results: [`load-test-results.md`](load-test-results.md).

## Target and pass/fail

- **Peak** = 200 concurrent exam takers. There is no production traffic yet; 200 is the
  stress level of the 2026-05-16 smoke test (`packages/backend/docs/pre_launch_checklist.md`).
  Replace it with the measured peak once production has a month of data.
- **Target** = peak × 2 = 400 virtual users. Stages: ramp to 200 (1m), hold (2m), ramp to
  400 (1m), hold (`HOLD`, default 3m), ramp down.
- Every VU reads each question 1–3 s and pauses 2–5 s between exams, so 400 VUs ≈ 400 real
  students, not 400 parallel connections.
- Thresholds (k6 exits non-zero when one is crossed):

| Metric | Threshold |
|---|---|
| `http_req_failed` | < 1% |
| `checks` | > 99% |
| `http_req_duration` | p95 < 800 ms, p99 < 2 s |
| login / list / start / answer / submit | p95 < 1500 / 500 / 1000 / 500 / 1000 ms |

## Run it

```bash
# 1. Production-settings stack (Postgres on tmpfs, Redis without persistence).
#    3 web replicas with the chart's per-pod limit (1 CPU, 1 GiB).
docker compose -p yuzdanyuz-loadtest -f loadtest/compose.yml up -d --build --wait --scale web=3

# 2. Seed 400 students, 40 open mocks x 10 questions (password only in your shell).
export LOADTEST_PASSWORD="lt-$(openssl rand -hex 8)"
docker compose -p yuzdanyuz-loadtest -f loadtest/compose.yml exec --index 1 \
  -e LOADTEST_PASSWORD web python /loadtest/seed_loadtest.py --users 400 --mocks 40 --questions 10

# 3. k6 inside the same network (it plays the ingress: X-Forwarded-For gives each VU its own IP).
docker run --rm --user 0 --network yuzdanyuz-loadtest_default -v "$PWD/loadtest:/loadtest" \
  -e BASE_URL=http://web:8000 -e LOADTEST_PASSWORD grafana/k6:1.8.2 run /loadtest/k6/core-flows.js
#    summary: loadtest/out/summary.json (p50/p95/p99 per step, error rate, thresholds)

# 4. Tear down: nothing is left on disk.
docker compose -p yuzdanyuz-loadtest -f loadtest/compose.yml down -v
```

Knobs: `PEAK_VUS` (default 200), `HOLD` (default `3m`), `IP_OFFSET` (fresh client IPs for a
repeated run: the login limit is 5/min per IP and blocks escalate).

Against staging: point `BASE_URL` at the staging host, drop `X-Forwarded-*` handling (the
real ingress sets them), seed with the same script in a staging pod, and run k6 from a
machine outside the cluster. Never seed or load-test production.

## What the run also proves

- celery-exporter (`http://celery-exporter:9808/metrics`) shows `celery_task_*_total` and
  `celery_queue_length` per queue, and the worker metrics server (`:9540`) shows
  `yz_exams_submitted_total`, the metrics the Celery dashboard and alerts use.
- Every response carries `X-Request-ID`.

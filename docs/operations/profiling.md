# Profiling — exam hot path (audit G09)

Measured 2026-11-26 with `packages/backend/scripts/profile_exam_flow.py` (cProfile + Django
`CaptureQueriesContext`) on Postgres 16 + Redis 7, Python 3.12, test settings, one process.
Flow: start a 40-question mock, answer all 40, submit, read the result.

```bash
cd packages/backend
python manage.py migrate && python manage.py shell < scripts/profile_exam_flow.py
```

## Results

| endpoint | calls | before: mean ms / SQL | after: mean ms / SQL |
|---|---|---|---|
| start (first request of a worker) | 1 | 1531 / 15 | **373** / 15 |
| answer | 40 | 20.2 / 19 | 20.1 / **18** |
| submit | 1 | 99 / 37 | 100 / **36** |
| result | 1 | 14.5 / 6 | 15.2 / 6 |

## Bottlenecks found

1. **Fixed — cold start 1.2 s.** `apps/catalog/services/ai_providers/claude.py` imported the
   Anthropic SDK at module level, and the module is reached from `core.urls`, so every worker
   paid ~1.2 s on its first request. The import is now inside the provider.
2. **Fixed — extra query per exam call.** `_get_user_attempt_or_404` did not join the exam,
   which the deadline check reads on every call.
3. **Open — per-statement RLS wrapper.** `core/db_rls.py` runs on every SQL statement (732 calls
   for 43 requests). It is the defense-in-depth layer; measure its share before changing it.
4. **Open — membership lookup per request.** `CustomUser.primary_membership` is resolved on
   every request (43 calls, ~3 ms each) by the tenant middleware; a per-request cache would
   remove it.
5. **Open — mastery update on every answer.** `exams.signals.update_mastery_on_answer` runs in
   the request (~3 ms per answer); it could move to the submit step or a task.
6. **Open — submit makes 36 queries** for 40 answers (scoring reads per answer); acceptable now,
   recheck with 100+ question mocks.

## In production

`SENTRY_PROFILES_SAMPLE_RATE` (fraction of traced transactions) is set in
`charts/yuzdanyuz/values-prod.yaml`, so real traffic is profiled continuously in Sentry.

# OWASP ZAP baseline scan

Workflow: [`.github/workflows/zap-baseline.yml`](../../.github/workflows/zap-baseline.yml),
weekly (Monday 02:30 UTC) and on manual dispatch. Reports: the `zap-reports` artifact of
each run (HTML, JSON, Markdown).

## What it scans

The backend from this commit with production settings (`loadtest/compose.yml`: image
built from `docker/Dockerfile.backend`, `core.settings.prod`, gunicorn, Postgres, Redis)
behind `edge`, a Caddy TLS proxy that plays the ingress (HTTPS with a local CA,
`X-Forwarded-Proto`/`-For`). Nothing outside the compose network is contacted: the API
scan pins the host with `-O`, so a `servers:` entry in the OpenAPI schema cannot redirect it.

1. `zap-baseline.py` — spider from `/admin/login/` (the only server-rendered HTML; `/`
   redirects to the Next.js frontend), passive rules only.
2. `zap-api-scan.py -S` — every operation in `/api/schema/` (OpenAPI), safe mode: passive
   rules only, no attack payloads.

The job fails on FAIL-level findings. Rule overrides with a reason and a date go in
[`loadtest/zap/rules.tsv`](../../loadtest/zap/rules.tsv) (`<id>\tIGNORE|WARN|FAIL\t<reason>`).

Run locally (same commands as the workflow):

```bash
docker compose -p zap -f loadtest/compose.yml up -d --build --wait web edge
mkdir -p loadtest/out/zap
docker run --rm --user 0 --network zap_default -v "$PWD/loadtest:/zap/wrk:rw" zaproxy/zap-stable:2.17.0 \
  zap-baseline.py -t https://edge.yuzdanyuz.test/admin/login/ -m 3 -I -j -c zap/rules.tsv -r out/zap/baseline.html
docker run --rm --user 0 --network zap_default -v "$PWD/loadtest:/zap/wrk:rw" zaproxy/zap-stable:2.17.0 \
  zap-api-scan.py -t https://edge.yuzdanyuz.test/api/schema/ -f openapi -S -I -c zap/rules.tsv \
  -O https://edge.yuzdanyuz.test -r out/zap/api.html
docker compose -p zap -f loadtest/compose.yml down -v
```

## 2026-11-12 scan (ZAP 2.17.0, this branch rebased on `d0067e9`)

| Pass | URLs | FAIL | WARN | PASS |
|---|---|---|---|---|
| Baseline (spider) | admin login, `/`, static, sitemap, robots | 0 | 11 | 57 |
| API (OpenAPI, safe mode) | 602 operations imported | 0 | 4 | 117 |

No High-risk alert. Triage of everything Medium and Low:

| Alert (id) | Risk | Where | Decision |
|---|---|---|---|
| CSP: no fallback / wildcard / `script-src` `unsafe-inline` (10055) | Medium | `/` and `/frontend/login` (302 redirects), `/robots.txt`, 404s of two API routes | Accepted: these responses carry the framing-only policy (`frame-ancestors 'none'; object-src 'none'; base-uri 'self'`, `core/middleware/csp.py`) and no executable content, so ZAP reads the missing `default-src` as "allow all". Follow-up: send `API_POLICY` on redirects and non-HTML errors to silence it. |
| CSP: `style-src 'unsafe-inline'` (10055) | Medium | Django admin pages | Accepted: admin templates use `style=""` attributes; scripts are nonce-only. |
| Cross-Domain Misconfiguration (10098) | Medium | `/static/admin/*.css` | Accepted: WhiteNoise sends `Access-Control-Allow-Origin: *` on public static files only; no cookies or user data are served there. |
| Cookie No HttpOnly Flag (10010) | Low | `csrftoken` | Accepted: Django's CSRF cookie is meant to be read by the page (double submit); session and JWT cookies are HttpOnly. |
| Permissions-Policy header not set (10063) | Low | HTML and API error responses | Follow-up: add next to the CSP header. |
| Cross-Origin-Embedder/Resource-Policy missing (90004) | Low | admin login, `/api/schema/`, sitemap | Accepted: no cross-origin isolated features are used. |
| Unexpected Content-Type (100001) | Low | `/api/analytics/export/<bad id>/`, `/api/analytics/finance/expenses/<bad id>/` | Minor: Django's HTML 404 for a malformed id instead of a JSON error. Follow-up. |

The first run the same day, before PR #63 (CSP) was merged, reported "CSP header not set"
on every HTML response; that alert is gone.

Informational alerts (authentication request identified, session response identified,
cacheable content, client-error codes on unauthenticated API calls) need no action.

# Production Deployment — Index

Task 10 — to'liq production stack uchun hujjatlar. Real cluster deploy yo'q,
faqat kod va manifestlar tayyor. Har bir fayl alohida bosqichni tushuntiradi.

## Bosqichlar

1. **[docker.md](docker.md)** — Local dev stack (`docker compose up`) va image build
2. **[k8s.md](k8s.md)** — Kubernetes manifestlari va Helm chart
3. **[argocd.md](argocd.md)** — GitOps deployment (Argo CD)
4. **[secrets.md](secrets.md)** — `kubectl create secret` orqali credentials
5. **[monitoring.md](monitoring.md)** — Sentry + Prometheus + OpenTelemetry
6. **[backup.md](backup.md)** — WAL-G PITR backup/restore runbook
7. **[cloudflare.md](cloudflare.md)** — DNS + WAF + Under Attack mode

## Tezkor reference

- **Domen**: `${PROD_DOMAIN}` placeholder. Real value `values-prod.yaml`'da.
- **Image registry**: `ghcr.io/menarzullayev/yuzdanyuz-{backend,nginx}`
- **GitOps**: Argo CD reconciles Helm chart from `main` branch
- **Defense-in-depth**: K8s NetworkPolicy + Cloudflare WAF + Django RateLimitMiddleware

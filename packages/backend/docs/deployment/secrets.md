# Secrets — `kubectl create secret`

Helm chart **secret content'ni boshqarmaydi**. Secret obyekti out-of-band yaratiladi
va `values.yaml`'da faqat reference qilinadi.

## Schema — `yuzdanyuz-secrets`

```bash
# Tartib: SECRET_KEY, DB password, Redis URL, OAuth credentials, payments, AI, ...
kubectl -n yuzdanyuz-prod create secret generic yuzdanyuz-secrets \
  --from-literal=SECRET_KEY='<django secret key — 50+ random bytes>' \
  --from-literal=DB_PASSWORD='<runtime role password (yuzdanyuz)>' \
  --from-literal=DB_MIGRATE_PASSWORD='<owner role password (yuzdanyuz_owner), migrate Job only>' \
  --from-literal=DB_BACKUP_PASSWORD='<backup role password (yuzdanyuz_backup)>' \
  --from-literal=FIELD_ENCRYPTION_KEYS='<Fernet key; see database-roles.md>' \
  --from-literal=REDIS_URL='redis://:<password>@redis-master:6379/1' \
  --from-literal=CHANNELS_REDIS_URL='redis://:<password>@redis-master:6379/2' \
  --from-literal=CELERY_BROKER_URL='redis://:<password>@redis-master:6379/0' \
  --from-literal=SENTRY_DSN='https://<key>@<org>.ingest.sentry.io/<project>' \
  --from-literal=GOOGLE_CLIENT_ID='<google oauth client id>' \
  --from-literal=GOOGLE_CLIENT_SECRET='<google oauth secret>' \
  --from-literal=YANDEX_CLIENT_ID='' \
  --from-literal=YANDEX_CLIENT_SECRET='' \
  --from-literal=APPLE_CLIENT_ID='' \
  --from-literal=APPLE_SECRET='' \
  --from-literal=APPLE_PRIVATE_KEY='' \
  --from-literal=TELEGRAM_BOT_TOKEN='<bot token from @BotFather>' \
  --from-literal=TELEGRAM_BOT_USERNAME='milsert_bot' \
  --from-literal=TELEGRAM_WEBHOOK_SECRET='<random token>' \
  --from-literal=PLAYMOBILE_LOGIN='<sms gateway login>' \
  --from-literal=PLAYMOBILE_PASSWORD='<sms gateway password>' \
  --from-literal=PAYME_MERCHANT_ID='<payme merchant id>' \
  --from-literal=PAYME_MERCHANT_KEY='<payme merchant key>' \
  --from-literal=CLICK_MERCHANT_ID='<click merchant id>' \
  --from-literal=CLICK_SECRET_KEY='<click secret>' \
  --from-literal=ANTHROPIC_API_KEY='<anthropic api key>' \
  --from-literal=CLAUDE_CODE_OAUTH_TOKEN='<token from: claude setup-token>'
```

No pod imports the whole Secret: each workload reads only its keys (`secretRef.common`
plus `secretRef.<workload>` in the chart values, one `secretKeyRef` per key), so a key a
workload does not list is never in its environment. A new key therefore has to be added
to the right list as well. Roles and passwords: [database-roles.md](database-roles.md).

`CLAUDE_CODE_OAUTH_TOKEN` is valid for one year. Its expiry date is not secret:
set `env.CLAUDE_CODE_OAUTH_TOKEN_EXPIRES` (YYYY-MM-DD) in the Helm values. The
system check (`intelligence.W001`–`W004`) and the daily
`intelligence.check_ai_token_expiry` task warn 30 days ahead; from 14 days the task
logs ERROR, which reaches Sentry. Renew: run `claude setup-token`, replace the secret
key and the date, then restart the workers.

Yoki `.env` faylidan to'g'ridan-to'g'ri (qulay, lekin file diskka tushadi):

```bash
kubectl -n yuzdanyuz-prod create secret generic yuzdanyuz-secrets \
  --from-env-file=secrets/prod.env
```

## GHCR pull secret

The `yuzdanyuz-backend` and `yuzdanyuz-nginx` images on GHCR are private
(since 2026-11-11: the backend image ships the Claude Code CLI, whose licence
does not allow public redistribution). Every pod pulls with `ghcr-pull`, which
`values-prod.yaml` sets as `global.imagePullSecrets`:

```bash
# A GitHub token with read:packages only (classic PAT or fine-grained).
kubectl -n yuzdanyuz-prod create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io \
  --docker-username='<github user>' \
  --docker-password='<token with read:packages>'
```

Locally, `docker compose` builds the images; pulling them needs `docker login ghcr.io`.

## Rotation

```bash
# 1. Yangi secret yarat (replace)
kubectl -n yuzdanyuz-prod create secret generic yuzdanyuz-secrets \
  --from-env-file=secrets/prod.env \
  --dry-run=client -o yaml | kubectl apply -f -

# 2. Pod'larni restart qilish (env vars container start'da yuklanadi)
kubectl -n yuzdanyuz-prod rollout restart deployment \
  yuzdanyuz-web yuzdanyuz-channels yuzdanyuz-celery-worker yuzdanyuz-celery-beat
```

## Production'da

Manual `kubectl create secret` GitOps-friendly emas. Production'da quyidagi
operator'lardan birini qo'shing (kelajak PR):

| Tool                    | Trade-off                                    |
|-------------------------|----------------------------------------------|
| Sealed Secrets (Bitnami)| Secrets git'da encrypted, CRD orqali decrypt|
| External Secrets Operator| Cloud KMS (AWS/GCP) bilan integratsiya     |
| HashiCorp Vault         | Eng kuchli, lekin operational overhead       |

## Audit

```bash
# Qaysi pod qaysi secret'ni mount qiladi
kubectl -n yuzdanyuz-prod get pods -o json | jq '.items[].spec.containers[] | {name, envFrom}'

# Secret oxirgi marta qachon yangilangan
kubectl -n yuzdanyuz-prod get secret yuzdanyuz-secrets -o jsonpath='{.metadata.creationTimestamp}'
```

## Hard rules

- ❌ `.env` fayllarini `git add` qilmang (`.gitignore`'da bor)
- ❌ Secret'larni Helm `values.yaml`'ga yozmang
- ❌ Secret'larni log'da ko'rsatmang (Django `LOGGING` config sensitive filter)
- ✅ SECRET_KEY ni har 90 kunda yangilang (sessions invalidate bo'ladi — coordinate)
- ✅ DB password ni quarterly rotate qiling

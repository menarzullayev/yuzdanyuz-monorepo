# Docker

Local development stack + image build pipeline.

## Komponentlar

```
docker/
├── Dockerfile.backend     multi-stage Python 3.11-slim → app + entrypoint
├── Dockerfile.nginx       Nginx reverse proxy + static
├── entrypoint.sh          web | asgi | worker | beat | migrate
└── nginx/
    ├── nginx.conf         JSON access log, gzip, keepalive
    └── default.conf       upstream backend + ws + /metrics + /health
```

## Local stack (Docker Compose)

```bash
docker compose up -d
docker compose logs -f backend
```

Servislar (lokal portlar):

| Service        | Port  | Purpose                       |
|----------------|-------|-------------------------------|
| backend (web)  | 8013  | Django gunicorn               |
| channels       | 8014  | Daphne ASGI (WebSocket)       |
| nginx          | 8080  | Reverse proxy                 |
| postgres       | 5992  | PostgreSQL 16                 |
| redis          | 6379  | Redis 7 (with password)       |
| clickhouse     | 8123  | ClickHouse HTTP               |
| meilisearch    | 7700  | Meilisearch                   |

```bash
# Web App: http://localhost:8013
# WebSocket: ws://localhost:8014/ws/...
# Behind nginx: http://localhost:8080
# Stop: docker compose down
# Wipe volumes: docker compose down -v
```

## Image variants

Bitta `Dockerfile.backend` 4 xil container yaratadi (entrypoint command'iga qarab):

```bash
# Web (gunicorn)
docker run --rm IMAGE web

# ASGI (daphne)
docker run --rm IMAGE asgi

# Celery worker
docker run --rm IMAGE worker

# Celery beat (scheduler)
docker run --rm IMAGE beat

# One-shot migrate + collectstatic
docker run --rm IMAGE migrate
```

Environment variables o'zgartirib runtime sozlanadi (gunicorn workers, celery
concurrency, OTel, ...). To'liq ro'yxat: [`docker/entrypoint.sh`](../../../../docker/entrypoint.sh).

## Build (manual)

```bash
docker build -f docker/Dockerfile.backend -t ghcr.io/menarzullayev/yuzdanyuz-backend:dev .
docker build -f docker/Dockerfile.nginx   -t ghcr.io/menarzullayev/yuzdanyuz-nginx:dev   .
```

CI/CD avtomatik yopadi:
[`.github/workflows/build-and-push.yml`](../../../../.github/workflows/build-and-push.yml).

## Healthchecks

- **Liveness**: `GET /health/` — har doim 200 (process ishlayapti)
- **Readiness**: `GET /health/ready/` — DB + Redis ping (503 agar biri yo'q bo'lsa)
- **Metrics**: `GET /metrics` — Prometheus exposition format

K8s probe'lar shu endpoint'larni ishlatadi
([`charts/yuzdanyuz/templates/deployment-web.yaml`](../../../../charts/yuzdanyuz/templates/deployment-web.yaml)).

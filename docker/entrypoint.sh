#!/usr/bin/env bash
# Container entrypoint — dispatches by command:
#   web         — gunicorn (WSGI) for HTTP API
#   asgi        — daphne (ASGI) for WebSocket consumers
#   worker      — celery worker
#   beat        — celery beat (scheduler)
#   flower      — celery flower (monitoring UI, optional)
#   migrate     — one-shot: apply migrations + collectstatic
#   shell       — interactive python manage.py shell
#
# OpenTelemetry: if OTEL_ENABLED=true, wraps the command with `opentelemetry-instrument`.

set -euo pipefail

CMD="${1:-web}"
shift || true

run() {
    if [[ "${OTEL_ENABLED:-false}" == "true" ]]; then
        exec opentelemetry-instrument "$@"
    else
        exec "$@"
    fi
}

case "$CMD" in
    web)
        # Run migrate-on-boot only when explicitly opted in; in K8s use a dedicated
        # init container or a separate Job to keep startup fast and avoid races
        # between multiple replicas applying migrations simultaneously.
        if [[ "${RUN_MIGRATIONS_ON_BOOT:-false}" == "true" ]]; then
            python manage.py migrate --noinput
        fi
        if [[ "${COLLECTSTATIC_ON_BOOT:-false}" == "true" ]]; then
            python manage.py collectstatic --noinput
        fi
        run gunicorn core.wsgi:application \
            --bind 0.0.0.0:8000 \
            --workers "${GUNICORN_WORKERS:-4}" \
            --threads "${GUNICORN_THREADS:-2}" \
            --timeout "${GUNICORN_TIMEOUT:-30}" \
            --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
            --access-logfile - \
            --error-logfile -
        ;;
    asgi)
        run daphne -b 0.0.0.0 -p 8000 core.asgi:application
        ;;
    worker)
        # -O fair: even task distribution among workers
        run celery -A core worker -l "${CELERY_LOG_LEVEL:-info}" \
            -Q "${CELERY_QUEUES:-default,celery}" \
            --concurrency="${CELERY_CONCURRENCY:-4}" \
            -O fair
        ;;
    beat)
        run celery -A core beat -l "${CELERY_LOG_LEVEL:-info}" \
            -S django_celery_beat.schedulers:DatabaseScheduler
        ;;
    flower)
        run celery -A core flower --port=5555
        ;;
    migrate)
        python manage.py migrate --noinput
        python manage.py collectstatic --noinput
        ;;
    shell)
        exec python manage.py shell
        ;;
    *)
        # Unknown command — exec it as-is (e.g., manage.py custom commands)
        exec "$CMD" "$@"
        ;;
esac

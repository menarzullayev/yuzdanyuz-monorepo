# Single-host production: docker compose + Cloudflare Tunnel

The first production (2026-11-27) runs on one Windows workstation with Docker Desktop:
`docker-compose.prod.yml` at the repository root, published only on `127.0.0.1`, and a
Cloudflare named tunnel as the one public entry. Kubernetes (`charts/yuzdanyuz`) stays
the target for a real server.

## Topology

```
visitor ─https─> Cloudflare ─tunnel─> cloudflared (host) ─> 127.0.0.1:8330 nginx
nginx ─> /api /accounts /admin /org /catalog /health /static /sitemap.xml ─> backend (gunicorn)
      ─> SSE /api/.../events/, /ws/ ─> channels (daphne)
      ─> everything else ─> frontend (Next.js standalone)
backend, channels, celery-worker, celery-ai, celery-beat ─> pgbouncer ─> postgres 16
                                                         ─> redis 7 (password, AOF)
migrate (one-shot, schema owner, direct to postgres) runs before every app service
```

* Roles and RLS: `docker/postgres/init-roles.sh` creates `yuzdanyuz_owner`,
  `yuzdanyuz` (runtime, `NOBYPASSRLS`), `yuzdanyuz_backup`, `yuzdanyuz_definer` on the
  first boot of an empty data volume ([database-roles.md](database-roles.md)).
* Data: named volumes `mashqgoh_pgdata`, `mashqgoh_redisdata`, `mashqgoh_mediadata`
  (fixed names, no anonymous volumes). **No backup yet** — decided "later"; it must exist
  before real users depend on the data ([backup.md](backup.md)).
* Client IP: cloudflared enters through the Docker gateway, so
  `TRUSTED_PROXY_CIDRS` is the compose subnet `10.99.77.0/24`; the client is the first
  untrusted hop of `X-Forwarded-For` (Cloudflare writes it).
* Plain HTTP: cloudflared forwards it with `X-Forwarded-Proto: http`; nginx answers 301
  to https for every path.
* `/metrics` is closed (404): every public request arrives from a private address here.
* Restart policy `always`: the stack comes back when Docker Desktop starts after a
  reboot (Docker Desktop itself starts at user logon).

## Secrets (outside git)

Two files in a directory readable only by the operator account:

| File | Read by | Keys |
|---|---|---|
| `compose.env` | `docker compose --env-file` (interpolation) | `APP_ENV_FILE`, `PUBLIC_PORT`, `POSTGRES_PASSWORD`, `DB_MIGRATE_PASSWORD`, `DB_PASSWORD`, `DB_BACKUP_PASSWORD`, `REDIS_PASSWORD` |
| `app.env` | every backend container (`env_file`) | `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `FRONTEND_BASE_URL`, `FIELD_ENCRYPTION_KEYS`, `TOTP_ENCRYPTION_KEY`, `TELEGRAM_WEBHOOK_SECRET`, `HEALTH_DEEP_TOKEN`, payment, bot, Google and AI slots |

Generate them with `secrets` / `os.urandom` straight into the files, never on a command
line or in output. Hex passwords for Postgres and Redis (they go into URLs).
`FIELD_ENCRYPTION_KEYS` must be set explicitly (see [secrets.md](secrets.md)).
Payments stay off: `ENABLE_REAL_PAYMENTS=false`, `BILLING_ENFORCE_EXAM_ACCESS=false`.

## Operate

Run from a checkout of the released commit (not a working tree other people edit):

```bash
export MSYS_NO_PATHCONV=1 GIT_COMMIT_SHA=$(git rev-parse HEAD)
dc() { docker compose -p mashqgoh -f docker-compose.prod.yml --env-file "$ENV_DIR/compose.env" "$@"; }
dc up -d --build          # build images, run migrate, start everything
dc ps                     # every service healthy / running, migrate "Exited (0)"
dc logs -f backend nginx
dc up -d --build backend  # after app.env changes: recreate the services that read it
scripts/smoke-deploy.sh https://mashqgoh.bugvector.uz
```

Never `dc down -v`: it deletes the production volumes.

Django admin user (the operator types the password; nothing is passed on the command line):

```bash
dc exec backend python manage.py createsuperuser
```

## Cloudflare Tunnel

A tunnel of its own (`mashqgoh`), separate from the other sites on this host, so
restarting it touches nobody else:

```
cloudflared tunnel create mashqgoh
cloudflared tunnel --config <dir>/mashqgoh.yml route dns <tunnel-id> mashqgoh.bugvector.uz
```

Pass `--config` (or the tunnel id) to `route dns`: with a default `config.yml` present,
cloudflared routes the hostname to the tunnel named in that file, not the one on the
command line. `mashqgoh.yml` maps the hostname to `http://127.0.0.1:8330` with a final
`http_status:404`. A scheduled task (at logon and every 5 minutes) starts the tunnel when
it is not running.

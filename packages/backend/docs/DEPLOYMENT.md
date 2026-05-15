# Milliy Sertifikat — Deployment & Infrastructure

## Overview

```
Production Stack:
├── Frontend: Nginx (reverse proxy, SSL/TLS)
├── App Layer: Django + Gunicorn
├── Cache/Session: Redis
├── Database: PostgreSQL (with WAL-G backups)
├── Analytics: ClickHouse
├── Search: Meilisearch
├── Monitoring: Prometheus + Grafana + Sentry
└── CDN: Cloudflare
```

---

## PostgreSQL Daemon (Local Development & Production)

### Daemon Script: daemon.sh

Located at `/home/hsm/apps/yuzdanyuz/scripts/daemon.sh`

```bash
#!/bin/bash
set -e

PG_HOME="/home/hsm/.local/pgsql"
RUN="$PG_HOME/run"
DATA="$PG_HOME/data"
LOG="$PG_HOME/postgres.log"

POSTGRES_USER="hsm"
POSTGRES_DB="yuzdanyuz_db"
POSTGRES_PASSWORD="hsm_secret_pass"

start() {
    echo "Starting PostgreSQL daemon..."
    
    # Create directories
    mkdir -p "$RUN" "$DATA"
    chmod 700 "$DATA"
    
    # Clean stale socket files
    rm -f "$RUN/.s.PGSQL"*
    
    # Check if already running
    if [ -f "$RUN/postmaster.pid" ]; then
        PID=$(cat "$RUN/postmaster.pid")
        if kill -0 "$PID" 2>/dev/null; then
            echo "PostgreSQL already running (PID: $PID)"
            return 0
        fi
        rm -f "$RUN/postmaster.pid"
    fi
    
    # Initialize DB if needed
    if [ ! -f "$DATA/PG_VERSION" ]; then
        echo "Initializing database cluster..."
        initdb -D "$DATA" -U "$POSTGRES_USER"
    fi
    
    # Start server
    exec postgres -D "$DATA" \
        -k "$RUN" \
        -p 5992 \
        -c log_statement=none \
        >> "$LOG" 2>&1 &
    
    DAEMON_PID=$!
    echo $DAEMON_PID > "$RUN/postmaster.pid"
    
    # Wait for socket
    sleep 1
    pg_isready -h "$RUN" -p 5992 -U "$POSTGRES_USER" || {
        echo "Failed to start PostgreSQL"
        return 1
    }
    
    # Set password if not set
    PGPASSWORD="$POSTGRES_PASSWORD" psql \
        -h "$RUN" -p 5992 -U "$POSTGRES_USER" \
        -c "ALTER USER $POSTGRES_USER WITH PASSWORD '$POSTGRES_PASSWORD';" || true
    
    echo "PostgreSQL started (PID: $DAEMON_PID)"
}

stop() {
    echo "Stopping PostgreSQL..."
    
    if [ -f "$RUN/postmaster.pid" ]; then
        PID=$(cat "$RUN/postmaster.pid")
        kill -TERM "$PID" 2>/dev/null || true
        
        # Wait for graceful shutdown
        for i in {1..30}; do
            if ! kill -0 "$PID" 2>/dev/null; then
                echo "PostgreSQL stopped"
                return 0
            fi
            sleep 1
        done
        
        # Force kill
        kill -9 "$PID" 2>/dev/null || true
        rm -f "$RUN/postmaster.pid"
    fi
}

restart() {
    stop
    sleep 2
    start
}

status() {
    if [ -f "$RUN/postmaster.pid" ]; then
        PID=$(cat "$RUN/postmaster.pid")
        if kill -0 "$PID" 2>/dev/null; then
            echo "PostgreSQL is running (PID: $PID)"
            pg_isready -h "$RUN" -p 5992 -U "$POSTGRES_USER"
            return 0
        fi
    fi
    echo "PostgreSQL is not running"
    return 1
}

case "${1:-start}" in
    start) start ;;
    stop) stop ;;
    restart) restart ;;
    status) status ;;
    *) echo "Usage: $0 {start|stop|restart|status}"; exit 1 ;;
esac
```

### Starting PostgreSQL

```bash
# Local development
bash /home/hsm/apps/yuzdanyuz/scripts/daemon.sh start

# Check status
bash /home/hsm/apps/yuzdanyuz/scripts/daemon.sh status

# View logs
tail -f /home/hsm/.local/pgsql/postgres.log

# Connect directly
psql -h /home/hsm/.local/pgsql/run -p 5992 -U hsm -d yuzdanyuz_db
```

---

## Redis Setup

### Installation

```bash
# Ubuntu/Debian
sudo apt-get install redis-server

# Or Docker
docker run -d --name redis -p 6379:6379 redis:7 \
    redis-server --requirepass foobared123
```

### Configuration (/etc/redis/redis.conf)

```conf
# Network
bind 127.0.0.1
port 6379

# Security
requirepass foobared123  # From .env: REDIS_PASSWORD

# Persistence
save 900 1               # Save if 1 change in 900 seconds
save 300 10              # Save if 10 changes in 300 seconds
appendonly yes           # AOF persistence

# Memory
maxmemory 2gb
maxmemory-policy allkeys-lru

# Replication (for HA)
replicaof 192.168.1.100 6379  # Point to primary in multi-node setup

# Logging
loglevel notice
logfile /var/log/redis/redis-server.log
```

### Starting Redis

```bash
# Standalone
redis-server /etc/redis/redis.conf

# As systemd service
sudo systemctl start redis-server
sudo systemctl enable redis-server

# Check status
redis-cli ping
# Should respond: PONG
```

### Redis CLI

```bash
# Connect
redis-cli -h 127.0.0.1 -p 6379 -a foobared123

# Monitor keys
MONITOR

# Check database
DBSIZE

# Flush (dangerous!)
FLUSHDB

# Monitor queue
LLEN celery  # Length of Celery queue
```

---

## Django Gunicorn Setup

### gunicorn.conf.py

```python
# gunicorn.conf.py
import multiprocessing

bind = ["127.0.0.1:8001"]
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"

max_requests = 1000
max_requests_jitter = 50

timeout = 30
keepalive = 5

accesslog = "/var/log/gunicorn/access.log"
errorlog = "/var/log/gunicorn/error.log"
loglevel = "info"

preload_app = True
```

### Starting Gunicorn

```bash
# Activate venv
source /home/hsm/apps/yuzdanyuz/venv/bin/activate

# Load env
export $(cat /home/hsm/apps/yuzdanyuz/.env | xargs)

# Start with auto-reload in dev
gunicorn config.wsgi:application \
    --bind 127.0.0.1:8001 \
    --workers 4 \
    --reload

# Production
gunicorn config.wsgi:application \
    --config gunicorn.conf.py \
    --daemon \
    --pid /var/run/gunicorn.pid
```

### systemd Service (Production)

Create `/etc/systemd/system/yuzdanyuz.service`:

```ini
[Unit]
Description=Milliy Sertifikat Django Application
After=network.target postgresql.service redis.service

[Service]
Type=notify
User=www-data
Group=www-data
WorkingDirectory=/home/hsm/apps/yuzdanyuz

Environment="PATH=/home/hsm/apps/yuzdanyuz/venv/bin"
EnvironmentFile=/home/hsm/apps/yuzdanyuz/.env

ExecStart=/home/hsm/apps/yuzdanyuz/venv/bin/gunicorn \
    config.wsgi:application \
    --bind 127.0.0.1:8001 \
    --workers 4 \
    --timeout 30

Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable yuzdanyuz
sudo systemctl start yuzdanyuz
sudo systemctl status yuzdanyuz
```

---

## Nginx Reverse Proxy

### nginx.conf

Located at `/etc/nginx/sites-available/yuzdanyuz` (symlink to `sites-enabled/`)

```nginx
upstream django {
    server 127.0.0.1:8001;
    keepalive 32;
}

server {
    listen 80;
    server_name hsm.sammu.uz www.hsm.sammu.uz;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name hsm.sammu.uz www.hsm.sammu.uz;

    # SSL/TLS (Let's Encrypt)
    ssl_certificate /etc/letsencrypt/live/hsm.sammu.uz/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/hsm.sammu.uz/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Logging
    access_log /var/log/nginx/yuzdanyuz_access.log;
    error_log /var/log/nginx/yuzdanyuz_error.log warn;

    # Root location
    location / {
        proxy_pass http://django;
        proxy_http_version 1.1;
        proxy_set_header Connection "keep-alive";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
    }

    # Staticfiles
    location /static/ {
        alias /home/hsm/apps/yuzdanyuz/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Media files
    location /media/ {
        alias /home/hsm/apps/yuzdanyuz/media/;
        expires 7d;
    }

    # Health check endpoint
    location /health/ {
        access_log off;
        return 200 "OK";
        add_header Content-Type text/plain;
    }
}
```

### Testing Nginx Config

```bash
# Validate syntax
sudo nginx -t

# Reload without downtime
sudo systemctl reload nginx

# Check status
sudo systemctl status nginx
```

---

## ClickHouse Analytics Database

### Docker Setup

```bash
docker run -d \
    --name clickhouse \
    -p 8123:8123 \
    -p 9000:9000 \
    -v clickhouse_data:/var/lib/clickhouse \
    -e CLICKHOUSE_DB=analytics \
    yandex/clickhouse-server:latest
```

### Schema Creation

```sql
CREATE DATABASE analytics;

CREATE TABLE analytics.exam_events (
    event_id UUID,
    user_id UUID,
    org_id UUID,
    exam_id UUID,
    event_type String,    -- 'exam_started', 'answer_submitted', 'exam_completed'
    score_value Int32,
    is_correct Boolean,
    duration_seconds Int32,
    timestamp DateTime,
    client_ip IPv4Address
)
ENGINE = MergeTree()
ORDER BY (timestamp, user_id)
PARTITION BY toYYYYMM(timestamp);

CREATE TABLE analytics.question_interactions (
    question_id UUID,
    user_id UUID,
    org_id UUID,
    interaction_type String,  -- 'viewed', 'answered', 'flagged'
    time_spent_seconds Int32,
    timestamp DateTime
)
ENGINE = MergeTree()
ORDER BY (timestamp, question_id)
PARTITION BY toYYYYMM(timestamp);
```

### Python Client

```python
# settings/base.py
CLICKHOUSE_CLIENT = {
    'host': config('CLICKHOUSE_HOST', default='localhost'),
    'port': config('CLICKHOUSE_PORT', default=8123, cast=int),
    'user': config('CLICKHOUSE_USER', default='default'),
    'password': config('CLICKHOUSE_PASSWORD', default=''),
    'database': 'analytics',
}

# tasks/analytics.py
from clickhouse_driver import Client
from django.conf import settings

def stream_event_to_clickhouse(event_data):
    """Send event to ClickHouse asynchronously."""
    client = Client(**settings.CLICKHOUSE_CLIENT)
    
    client.execute(
        'INSERT INTO exam_events VALUES',
        [event_data]
    )
```

---

## SSL/TLS with Let's Encrypt

### Automatic Renewal

```bash
# Install Certbot
sudo apt-get install certbot python3-certbot-nginx

# Get certificate
sudo certbot certonly --nginx -d hsm.sammu.uz

# Auto-renewal (runs daily)
sudo systemctl enable certbot.timer
sudo systemctl start certbot.timer

# Test renewal
sudo certbot renew --dry-run
```

---

## Backups & Recovery

### PostgreSQL Backups with WAL-G

WAL-G enables Point-in-Time Recovery (PITR):

```bash
# Install WAL-G
wget https://github.com/wal-g/wal-g/releases/download/v2.0.1/wal-g-pg-ubuntu-20.04-amd64.tar.gz
tar -zxf wal-g-pg-ubuntu-20.04-amd64.tar.gz
sudo mv wal-g /usr/local/bin/

# Configure for S3 storage
export WALG_S3_PREFIX=s3://backup-bucket/yuzdanyuz/
export AWS_ACCESS_KEY_ID=xxx
export AWS_SECRET_ACCESS_KEY=xxx
export AWS_DEFAULT_REGION=us-east-1

# Create backup
wal-g backup-push

# Restore to point-in-time
wal-g backup-fetch /var/lib/postgresql/recover LATEST
wal-g wal-fetch /var/lib/postgresql/pg_wal
```

### Daily Backup Script

```bash
#!/bin/bash
# /usr/local/bin/pg_backup_daily.sh

BACKUP_DIR="/backups/postgresql"
RETENTION_DAYS=30

# Full backup
pg_dump -h /home/hsm/.local/pgsql/run -p 5992 -U hsm yuzdanyuz_db | \
    gzip > $BACKUP_DIR/yuzdanyuz_$(date +%Y%m%d).sql.gz

# Cleanup old backups
find $BACKUP_DIR -name "*.sql.gz" -mtime +$RETENTION_DAYS -delete

echo "Backup completed at $(date)"
```

### Cron Job

```bash
# Daily at 3 AM
0 3 * * * /usr/local/bin/pg_backup_daily.sh
```

---

## Monitoring & Health Checks

### Prometheus Scrape Config

```yaml
# /etc/prometheus/prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'django'
    static_configs:
      - targets: ['localhost:8001']
    metrics_path: '/metrics/'

  - job_name: 'postgres'
    static_configs:
      - targets: ['localhost:9187']

  - job_name: 'redis'
    static_configs:
      - targets: ['localhost:9121']

  - job_name: 'nginx'
    static_configs:
      - targets: ['localhost:9113']
```

### Django Metrics Export

```python
# core/middleware.py
from prometheus_client import Counter, Histogram
import time

request_count = Counter(
    'django_requests_total',
    'Total HTTP requests',
    ['method', 'status']
)

request_duration = Histogram(
    'django_request_duration_seconds',
    'Request latency'
)

class PrometheusMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        start = time.time()
        
        response = self.get_response(request)
        
        duration = time.time() - start
        request_count.labels(
            method=request.method,
            status=response.status_code
        ).inc()
        request_duration.observe(duration)
        
        return response
```

### Grafana Dashboard

Key metrics to monitor:
- **Django**: Request latency, error rate, active users
- **PostgreSQL**: Query latency, connection count, cache hit ratio
- **Redis**: Memory usage, operations/sec, evictions
- **Celery**: Queue length, task success rate, worker health
- **System**: CPU, memory, disk I/O, network

### Health Check Endpoint

```python
# core/views.py
from django.http import JsonResponse
from django.db import connection
from django_redis import get_redis_connection

def health_check(request):
    """System health check."""
    status = {
        'status': 'ok',
        'services': {}
    }
    
    # Check database
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        status['services']['database'] = 'ok'
    except Exception as e:
        status['services']['database'] = f'error: {e}'
        status['status'] = 'degraded'
    
    # Check Redis
    try:
        redis_conn = get_redis_connection('default')
        redis_conn.ping()
        status['services']['redis'] = 'ok'
    except Exception as e:
        status['services']['redis'] = f'error: {e}'
        status['status'] = 'degraded'
    
    # Check Celery
    try:
        from config.celery import app
        stats = app.control.inspect().stats()
        if stats:
            status['services']['celery'] = 'ok'
        else:
            status['services']['celery'] = 'no workers'
            status['status'] = 'degraded'
    except Exception as e:
        status['services']['celery'] = f'error: {e}'
        status['status'] = 'degraded'
    
    code = 200 if status['status'] == 'ok' else 503
    return JsonResponse(status, status=code)

# URLs
urlpatterns = [
    path('health/', health_check, name='health_check'),
]
```

---

## Disaster Recovery Plan

### Recovery Procedures

**Database Corruption**:
1. Stop application: `sudo systemctl stop yuzdanyuz`
2. Restore from backup: `wal-g backup-fetch /data/recover LATEST`
3. Replay WAL logs: `wal-g wal-fetch /data/pg_wal`
4. Start PostgreSQL: `bash /home/hsm/apps/yuzdanyuz/scripts/daemon.sh start`
5. Run migrations: `python manage.py migrate`
6. Start application: `sudo systemctl start yuzdanyuz`

**Redis Data Loss**:
1. Redis persists to disk (AOF)
2. If lost, restart from backup
3. Celery retries failed tasks (3 attempts)
4. Session data recreated on next login

**Disk Space Full**:
1. Check: `df -h`
2. Cleanup old logs: `find /var/log -name "*.log" -mtime +30 -delete`
3. Cleanup old backups: `find /backups -mtime +30 -delete`
4. Check Celery queue: `redis-cli DBSIZE`

---

## Production Checklist

- [ ] HTTPS enforced (SSL/TLS certificate)
- [ ] Database backups automated (daily with PITR)
- [ ] Redis persistence enabled (AOF)
- [ ] Monitoring active (Prometheus + Grafana)
- [ ] Alerting configured (email/Slack)
- [ ] Log aggregation setup (Sentry, ELK)
- [ ] Firewall rules configured
- [ ] Rate limiting enabled
- [ ] CORS headers set
- [ ] Secret management (.env not in git)
- [ ] Load balancer configured (if multi-server)
- [ ] Health checks on all services
- [ ] Documentation updated
- [ ] Disaster recovery tested

---

## CI/CD with GitHub Actions

### .github/workflows/deploy.yml

```yaml
name: Deploy to Production

on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:14
        env:
          POSTGRES_DB: test_db
          POSTGRES_USER: user
          POSTGRES_PASSWORD: pass
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 5432:5432
      redis:
        image: redis:7
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 6379:6379

    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements/dev.txt
      
      - name: Run migrations
        env:
          DATABASE_URL: postgresql://user:pass@localhost:5432/test_db
        run: python manage.py migrate
      
      - name: Run tests
        env:
          DATABASE_URL: postgresql://user:pass@localhost:5432/test_db
        run: pytest --cov=. --cov-report=xml
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3

  deploy:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Deploy to production
        env:
          SSH_PRIVATE_KEY: ${{ secrets.SSH_PRIVATE_KEY }}
          DEPLOY_HOST: ${{ secrets.DEPLOY_HOST }}
          DEPLOY_USER: ${{ secrets.DEPLOY_USER }}
        run: |
          mkdir -p ~/.ssh
          echo "$SSH_PRIVATE_KEY" > ~/.ssh/id_ed25519
          chmod 600 ~/.ssh/id_ed25519
          ssh -o StrictHostKeyChecking=no $DEPLOY_USER@$DEPLOY_HOST "cd /home/hsm/apps/yuzdanyuz && git pull origin main && python manage.py migrate && sudo systemctl restart yuzdanyuz"
```

---

## Environment Variables for Production

```bash
# .env (production)
DEBUG=False
DJANGO_ENV=prod
SECRET_KEY=your-production-secret-key-here

# Database
DB_HOST=/home/hsm/.local/pgsql/run
DB_PORT=5992
DB_NAME=yuzdanyuz_db
DB_USER=hsm
DB_PASSWORD=secure_password_here

# Redis
REDIS_PASSWORD=secure_redis_password

# Celery
CELERY_BROKER_URL=redis://:secure_redis_password@127.0.0.1:6379/1
CELERY_RESULT_BACKEND=redis://:secure_redis_password@127.0.0.1:6379/2

# SSL
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True

# External services
GOOGLE_OAUTH_CLIENT_ID=xxx
GOOGLE_OAUTH_SECRET=xxx
TELEGRAM_BOT_TOKEN=xxx
ANTHROPIC_API_KEY=sk-ant-xxx
```

Store secrets securely:
- Use environment variable management service (Doppler, Vault)
- Never commit `.env` to git (use `.env.example`)
- Rotate secrets regularly
- Use separate keys for dev/staging/prod

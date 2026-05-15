# Milliy Sertifikat — Technology Stack & Configuration

## Core Framework

| Component | Version | Purpose | Notes |
|-----------|---------|---------|-------|
| **Django** | 5.2 | Web framework | Class-based & function-based views, ORM, middleware |
| **Python** | 3.11+ | Runtime | Async support via `async_to_sync`, `sync_to_async` |
| **PostgreSQL** | 14+ | Primary database | Socket connection, RLS enabled, JSONB for flexible schemas |
| **Redis** | 7+ | Cache & session store | Password: `foobared123` (from .env), port 6379 |
| **Celery** | 5.3+ | Async task queue | Redis broker, default queue name: `celery` |

---

## Frontend Stack

### HTMX + Alpine.js (No SPA)

```python
# requirements/base.txt
django==5.2.0
django-htmx==1.17.0  # hx-* attribute support
```

```html
<!-- base.html template structure -->
<html>
  <head>
    <script defer src="https://unpkg.com/htmx.org@1.9.10"></script>
    <script defer src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js"></script>
    <link rel="stylesheet" href="/static/css/tailwind.css">
  </head>
  <body hx-boost="true" hx-indicator=".htmx-request .htmx-indicator">
    {% include "components/navbar.html" %}
    {% block content %}{% endblock %}
  </body>
</html>
```

### HTMX Core Concepts

**Request Pattern**:
```html
<div hx-get="/api/endpoint/" 
     hx-trigger="load, customEvent from:body"
     hx-swap="innerHTML swap:1s"
     hx-target="#target-id"
     hx-select=".partial-class">
  Loading...
</div>
```

**Response Handling**:
- **Status 200**: HTMX swaps the response into DOM
- **Status 400**: Treated as error; no swap unless `hx-swap="innerHTML"`
- **Status 422**: Validation errors, HTMX renders error partial in place
- **HX-Redirect**: Server responds with `HX-Redirect: /url/` header (client-side navigation)
- **HX-Trigger**: Server sends `HX-Trigger: "event-name"` to trigger client-side event

**Out-of-Band (OOB) Swaps**:
```html
<!-- Server response includes elements with hx-swap-oob -->
<div hx-swap-oob="innerHTML:#sidebar">
  <!-- This replaces #sidebar even though hx-target was #main-content -->
  Updated sidebar content
</div>

<div id="main-content">
  Main content here
</div>
```

### Tailwind CSS

```python
# settings/base.py
TAILWIND_APP_NAME = 'theme'
INSTALLED_APPS = [
    'django_tailwind',
    ...
]
```

Build CSS:
```bash
python manage.py tailwind build
python manage.py tailwind start  # Watch mode in dev
```

### Alpine.js State Management

```html
<div x-data="{ 
  count: 0, 
  toggle: false,
  fetch_loading: false 
}">
  <button @click="count++" x-text="`Count: ${count}`"></button>
  
  <button @click="toggle = !toggle" 
          :class="toggle ? 'bg-blue-500' : 'bg-gray-500'">
    Toggle
  </button>
  
  <!-- Fetch with Alpine -->
  <button @click="fetch_loading = true; 
    fetch('/api/endpoint/')
      .then(r => r.text())
      .then(html => { /* process */ fetch_loading = false })">
    Fetch Data
  </button>
  
  <!-- Watch nested object -->
  <input x-model="filters.search" @input.debounce="$dispatch('filters-changed')">
</div>
```

---

## Backend Stack

### Django Apps (Domain-Driven Design)

```
apps/
├── accounts/             # User authentication & profiles
├── organizations/        # Multi-tenant org structure (RBAC)
├── catalog/             # Question bank, imports, reviews
├── exams/               # Mock exams, practice sessions
├── intelligence/        # AI diagnostics, knowledge graph
├── commerce/            # Wallet, billing, subscriptions
├── engagement/          # Streaks, leagues, badges
├── analytics/           # ClickHouse event streaming
└── core/                # Shared: middleware, managers, mixins
```

### Django Configuration

**settings/base.py Structure**:
```python
# Environment
import os
from decouple import config

DEBUG = config('DEBUG', default=False, cast=bool)
SECRET_KEY = config('SECRET_KEY')
ALLOWED_HOSTS = config('ALLOWED_HOSTS', cast=lambda v: [s.strip() for s in v.split(',')])

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME'),
        'USER': config('DB_USER'),
        'PASSWORD': config('DB_PASSWORD'),
        'HOST': config('DB_HOST'),     # Socket path: /home/hsm/.local/pgsql/run
        'PORT': config('DB_PORT'),     # Socket port: 5992
        'CONN_MAX_AGE': 600,
        'OPTIONS': {
            'sslmode': 'disable',
        }
    }
}

# Redis
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': f"redis://:{config('REDIS_PASSWORD')}@127.0.0.1:6379/0",
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'SOCKET_CONNECT_TIMEOUT': 5,
            'SOCKET_TIMEOUT': 5,
            'COMPRESSOR': 'django_redis.compressors.zlib.ZlibCompressor',
            'IGNORE_EXCEPTIONS': True,
        }
    }
}

# Celery
CELERY_BROKER_URL = f"redis://:{config('REDIS_PASSWORD')}@127.0.0.1:6379/1"
CELERY_RESULT_BACKEND = f"redis://:{config('REDIS_PASSWORD')}@127.0.0.1:6379/2"
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TIMEZONE = 'UTC'

# Installed Apps
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third-party
    'django_extensions',
    'django_htmx',
    'django_tailwind',
    'rest_framework',
    'corsheaders',
    'django_celery_beat',
    'django_celery_results',
    
    # Local apps
    'core',
    'accounts',
    'organizations',
    'catalog',
    'exams',
    'intelligence',
    'commerce',
    'engagement',
    'analytics',
]

# Middleware
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'core.middleware.TenantMiddleware',        # Multi-tenancy context
    'core.middleware.DeviceFingerprintMiddleware',  # Session locking
]

# Template Engines
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.tenant_context',
            ],
        },
    },
]
```

**settings/dev.py**:
```python
from .base import *

DEBUG = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'hsm.sammu.uz']

# Celery in-memory for dev (optional)
# CELERY_TASK_ALWAYS_EAGER = True

# Django Debug Toolbar
INSTALLED_APPS += ['debug_toolbar']
MIDDLEWARE += ['debug_toolbar.middleware.DebugToolbarMiddleware']
INTERNAL_IPS = ['127.0.0.1']

# Disable cache in dev
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
    }
}
```

**settings/prod.py**:
```python
from .base import *

DEBUG = False
ALLOWED_HOSTS = ['hsm.sammu.uz', 'www.hsm.sammu.uz']

# HTTPS
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Security headers
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': '/var/log/django/yuzdanyuz.log',
            'maxBytes': 1024 * 1024 * 10,  # 10MB
            'backupCount': 10,
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['file'],
        'level': 'INFO',
    },
}
```

---

## External Services & APIs

### Authentication

| Service | Purpose | Configuration |
|---------|---------|----------------|
| **Google OAuth 2.0** | Web login (accounts/google/) | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_SECRET` |
| **Telegram Bot API** | Telegram login + deep-link sign-in | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` |
| **PlayMobile SMS** | OTP delivery (Uzbekistan) | `PLAYMOBILE_API_KEY`, `PLAYMOBILE_SERVICE_ID` |
| **Eskiz SMS** | Backup OTP provider | `ESKIZ_API_KEY`, `ESKIZ_EMAIL` |

### Payment

| Service | Purpose | Configuration |
|---------|---------|----------------|
| **Payme** | Card payments (UZS) | `PAYME_MERCHANT_ID`, `PAYME_API_KEY` |
| **Click** | Alternative payment gateway | `CLICK_SERVICE_ID`, `CLICK_MERCHANT_ID`, `CLICK_SECRET_KEY` |

### Analytics & AI

| Service | Purpose | Configuration |
|---------|---------|----------------|
| **Anthropic API** | Question parsing + AI tutor | `ANTHROPIC_API_KEY` |
| **OpenAI API** | Fallback AI service | `OPENAI_API_KEY` (optional) |
| **ClickHouse** | Analytics database | `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD` |

---

## Environment Variables (.env)

```bash
# Django
DEBUG=False
SECRET_KEY=your-secret-key-here
ALLOWED_HOSTS=hsm.sammu.uz,localhost

# Database (PostgreSQL socket)
DB_HOST=/home/hsm/.local/pgsql/run
DB_PORT=5992
DB_NAME=yuzdanyuz_db
DB_USER=hsm
DB_PASSWORD=hsm_secret_pass

# Redis
REDIS_PASSWORD=foobared123

# Celery
CELERY_BROKER_URL=redis://:foobared123@127.0.0.1:6379/1
CELERY_RESULT_BACKEND=redis://:foobared123@127.0.0.1:6379/2

# Google OAuth
GOOGLE_OAUTH_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_OAUTH_SECRET=xxx

# Telegram
TELEGRAM_BOT_TOKEN=xxx:yyy
TELEGRAM_BOT_USERNAME=milliy_sertifikat_bot

# SMS Providers
PLAYMOBILE_API_KEY=xxx
PLAYMOBILE_SERVICE_ID=xxx
ESKIZ_API_KEY=xxx
ESKIZ_EMAIL=admin@example.com

# Payment
PAYME_MERCHANT_ID=xxx
PAYME_API_KEY=xxx
CLICK_SERVICE_ID=xxx
CLICK_MERCHANT_ID=xxx
CLICK_SECRET_KEY=xxx

# AI
ANTHROPIC_API_KEY=sk-ant-xxx
OPENAI_API_KEY=sk-xxx  # Optional fallback

# ClickHouse Analytics
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=

# Application
SITE_URL=https://hsm.sammu.uz/yuzdanyuz/
ADMIN_EMAIL=admin@example.com
```

---

## Dependencies by Category

### Base Requirements (`requirements/base.txt`)

```
Django==5.2.0
django-extensions==3.2.3
django-htmx==1.17.0
django-redis==5.4.0
django-cors-headers==4.3.1
psycopg2-binary==2.9.9
celery==5.3.4
redis==5.0.1
Pillow==10.1.0
openpyxl==3.11.0
requests==2.31.0
python-decouple==3.8
PyJWT==2.8.1
python-telegram-bot==20.3
djangorestframework==3.14.0
django-filter==23.5
django-celery-beat==2.5.0
django-celery-results==2.5.1
clickhouse-driver==0.4.6
```

### Development Requirements (`requirements/dev.txt`)

```
-r base.txt
Django==5.2.0
django-debug-toolbar==4.2.0
django-extensions==3.2.3
pytest==7.4.3
pytest-django==4.7.0
pytest-cov==4.1.0
black==23.12.1
flake8==6.1.0
isort==5.13.2
bandit==1.7.5
```

### Production Requirements (`requirements/prod.txt`)

```
-r base.txt
gunicorn==21.2.0
whitenoise==6.6.0
```

---

## Local Development Setup

### 1. Start PostgreSQL Daemon

```bash
bash /home/hsm/apps/yuzdanyuz/scripts/daemon.sh start
```

Verify:
```bash
pg_isready -h /home/hsm/.local/pgsql/run -p 5992 -U hsm
```

### 2. Start Redis

```bash
redis-server --port 6379 --requirepass foobared123
```

### 3. Load Environment

```bash
cd /home/hsm/apps/yuzdanyuz
export $(cat .env | xargs)
```

### 4. Apply Migrations

```bash
python manage.py migrate
```

### 5. Start Django

```bash
python manage.py runserver 127.0.0.1:8001
```

### 6. Start Celery (separate terminal)

```bash
celery -A config.celery worker -l info
```

### 7. Start Celery Beat (separate terminal, for scheduled tasks)

```bash
celery -A config.celery beat -l info
```

---

## Project Structure

```
/home/hsm/apps/yuzdanyuz/
├── config/                       # Django settings & Celery config
│   ├── settings/
│   │   ├── base.py              # Shared settings
│   │   ├── dev.py               # Development overrides
│   │   └── prod.py              # Production overrides
│   ├── celery.py                # Celery app config
│   ├── wsgi.py                  # WSGI entry point
│   └── urls.py                  # Root URL routing
│
├── apps/                         # Domain-driven design
│   ├── accounts/
│   │   ├── models.py
│   │   ├── views.py
│   │   ├── serializers.py
│   │   └── urls.py
│   ├── organizations/
│   ├── catalog/
│   ├── exams/
│   ├── intelligence/
│   ├── commerce/
│   ├── engagement/
│   ├── analytics/
│   └── core/
│       ├── managers.py           # TenantManager, GlobalManager
│       ├── mixins.py             # TenantTimestampMixin
│       ├── middleware.py         # TenantMiddleware, DeviceFingerprintMiddleware
│       └── tenant.py             # tenant_context context manager
│
├── templates/                    # HTMX templates
│   ├── base.html                # Master template
│   ├── accounts/
│   ├── catalog/
│   └── components/              # Reusable partials
│
├── static/
│   ├── css/
│   │   └── tailwind.css          # Generated by tailwind build
│   └── js/
│       └── htmx.min.js           # HTMX library (inline or cdn)
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── TECH_STACK.md
│   ├── DATABASE.md
│   ├── API.md
│   ├── SECURITY.md
│   ├── CELERY_TASKS.md
│   ├── TESTING.md
│   └── DEPLOYMENT.md
│
├── design/                       # v0.dev wireframes (DO NOT MODIFY)
│   ├── index.html
│   ├── app.jsx
│   ├── design-canvas.jsx
│   ├── tweaks-panel.jsx
│   ├── primitives.jsx
│   ├── frames-desktop.jsx
│   └── frames-mobile.jsx
│
├── scripts/
│   └── daemon.sh                 # PostgreSQL daemon manager
│
├── manage.py
├── .env                          # Environment variables
├── .env.example                  # Template
├── Makefile                      # Development shortcuts
├── requirements.txt              # All dependencies
└── README.md
```

---

## Quick Commands

### Makefile Shortcuts

```bash
make run              # Start Django + Celery in separate terminals
make migrate          # Apply database migrations
make migrations       # Create new migration files
make test             # Run test suite
make lint             # Check code style
make format           # Auto-format code
make shell            # Django interactive shell
make createsuperuser  # Create admin user
make dbshell          # PostgreSQL psql shell
```

### Common Django Commands

```bash
# Database
python manage.py makemigrations --app catalog
python manage.py migrate
python manage.py dbshell                    # psql

# Admin
python manage.py createsuperuser
python manage.py changepassword <username>

# Development
python manage.py runserver
python manage.py shell
python manage.py shell_plus                 # Enhanced shell (django-extensions)
python manage.py runscript scripts/seed.py  # Run custom scripts

# Static Files
python manage.py collectstatic --noinput
python manage.py findstatic

# Monitoring
python manage.py check                      # System checks
python manage.py diffsettings --default=django.conf.global_settings
```

---

## Version Notes

- **Django 5.2**: Latest LTS candidate, async support via `asgiref`
- **Python 3.11+**: Type hints, match/case, performance improvements
- **PostgreSQL 14+**: JIT compilation, B-tree deduplication
- **Redis 7+**: ACL support (multi-user), improved replication
- **Celery 5.3+**: Better task routing, result backend persistence

All packages are pinned to specific versions in `requirements/base.txt` for reproducibility.

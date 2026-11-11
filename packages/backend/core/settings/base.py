import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'not-secret-key-for-testing-only')

INSTALLED_APPS = [
    'django.contrib.admin',
    # ISSUE-401: SOC2/ISO 27001 audit trail — historical_<model> shadow tables
    'simple_history',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # django-allauth
    'django.contrib.sites',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    # SEO (Task 9)
    'django.contrib.sitemaps',
    # Observability (Task 10)
    'django_prometheus',
    # Celery infrastructure (admin'da beat schedule + result tracking)
    'django_celery_beat',
    'django_celery_results',
    # DRF — REST API
    'rest_framework',
    # ISSUE-201: OpenAPI 3.0 schema
    'drf_spectacular',
    # CORS for frontend (Next.js localhost:3000 in dev, Vercel domain in prod)
    'corsheaders',
    # ISSUE-505: Feature flags (django-flags) — per-user/per-org/percentage rollout
    'flags',
    # Channels — WebSocket (heartbeat, real-time strikes)
    'channels',
    # Local apps — Domain-Driven Design
    'apps.accounts',  # User, auth, device session
    'apps.organizations',  # Organization (tenant), membership, roles
    'apps.catalog',  # Question, QuestionBank, Tag, AnswerChoice
    'apps.exams',  # MockExam, PracticeSession, UserAnswer, AntiCheat
    'apps.intelligence',  # KnowledgeGraph, SkillTag, StudyPlan, AIFeedback
    'apps.commerce',  # Wallet, Transaction, Subscription, Affiliate
    'apps.engagement',  # Streak, League, Badge, Notification
    'apps.analytics',  # ClickHouseEvent, Report, B2BDashboard
    'apps.webhooks',  # ISSUE-405: B2B outgoing webhooks (HMAC-signed, retry + DLQ)
]

MIDDLEWARE = [
    # Prometheus — must be FIRST (before) and LAST (after) for accurate timing
    'django_prometheus.middleware.PrometheusBeforeMiddleware',
    'core.middleware.fix_prefix.ForceScriptNameMiddleware',
    'django.middleware.security.SecurityMiddleware',
    # WhiteNoise — serve /static/ in WSGI without nginx (works under gunicorn/daphne)
    'whitenoise.middleware.WhiteNoiseMiddleware',
    # CORS — must come BEFORE CommonMiddleware to add headers on every response
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'core.middleware.jwt_auth.JWTAuthMiddleware',  # 1. cookie → request.user
    'apps.accounts.middleware.DeviceCheckMiddleware',  # 2. fingerprint tekshirish
    'allauth.account.middleware.AccountMiddleware',  # allauth required
    'core.middleware.jwt_cookie_writer.JWTCookieWriterMiddleware',  # allauth→JWT bridge
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.accounts.middleware.DeviceLogoutMiddleware',  # force logout → cookie tozalash
    'core.middleware.tenant.TenantMiddleware',  # user → org context
    # ISSUE-409: per-request actor (current user) ContextVar for AuditUserMixin
    'core.middleware.audit_user.AuditUserMiddleware',
    # ISSUE-401: django-simple-history — stamps HistoricalX rows with request.user
    # Must come AFTER AuditUserMiddleware so request.user is already resolved.
    'simple_history.middleware.HistoryRequestMiddleware',
    'core.middleware.rls_middleware.RLSMiddleware',  # PostgreSQL RLS setup (DB-level isolation)
    'core.middleware.rate_limit.RateLimitMiddleware',  # Redis-backed rate limiting (HTTP 429)
    # ISSUE-X04: CDN/edge Cache-Control headers for anonymous GETs (sitemap, leaderboard)
    'core.middleware.cdn_cache.CDNCacheMiddleware',
    # ISSUE-203: Sunset + Deprecation header for legacy /api/ paths
    'core.middleware.api_deprecation.APIDeprecationMiddleware',
    'django_prometheus.middleware.PrometheusAfterMiddleware',  # closes timing for metrics
]

AUTHENTICATION_BACKENDS = [
    'apps.accounts.backends.JWTBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
    'django.contrib.auth.backends.ModelBackend',
]

SITE_ID = 1

REDIS_URL = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/1')

# ── Client IP / brute-force protection ────────────────────────
# Comma-separated CIDRs of our own reverse proxies (ingress/nginx, and
# Cloudflare ranges if they can reach the origin). CF-Connecting-IP and
# X-Forwarded-For are only believed when REMOTE_ADDR is in this list; empty
# means trust nothing and use REMOTE_ADDR. See core/utils/client_ip.py.
TRUSTED_PROXY_CIDRS = [
    c.strip() for c in os.getenv('TRUSTED_PROXY_CIDRS', '').split(',') if c.strip()
]

# Per-account password-login backoff (core/utils/login_throttle.py).
LOGIN_FAILURE_FREE_ATTEMPTS = int(os.getenv('LOGIN_FAILURE_FREE_ATTEMPTS', '5'))
# After the free attempts: 1 min, then 5 min, then 15 min (cap) per further failure.
LOGIN_LOCK_LADDER_SECONDS = (60, 300, 900)
LOGIN_FAILURE_WINDOW_SECONDS = int(os.getenv('LOGIN_FAILURE_WINDOW_SECONDS', '3600'))

# ── Celery ────────────────────────────────────────────────────
# Worker: `celery -A core worker -l info`
# Beat:   `celery -A core beat -l info -S django_celery_beat.schedulers:DatabaseScheduler`
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', REDIS_URL)
CELERY_RESULT_BACKEND = 'django-db'  # django_celery_results
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TIMEZONE = 'Asia/Tashkent'
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300  # 5 daqiqa hard limit
CELERY_TASK_SOFT_TIME_LIMIT = 240  # 4 daqiqada SoftTimeLimitExceeded

# ── Django REST Framework ─────────────────────────────────────
# JWT cookie auth allaqachon JWTAuthMiddleware tomonidan handle qilinadi —
# request.user view'ga yetib kelguncha autentifikatsiya qilingan bo'ladi.
# DRF SessionAuthentication shu user'ni qabul qiladi.
# ── Django Channels ───────────────────────────────────────────
ASGI_APPLICATION = 'core.asgi.application'
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            # Channels uchun alohida Redis db (token sessions DB=1, channels DB=2)
            'hosts': [os.getenv('CHANNELS_REDIS_URL', 'redis://127.0.0.1:6379/2')],
        },
    },
}

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'EXCEPTION_HANDLER': 'core.exceptions.unified_exception_handler',
    # ISSUE-201: drf-spectacular schema generator
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# ISSUE-201: drf-spectacular config
SPECTACULAR_SETTINGS = {
    'TITLE': 'YuzDanYuz Backend API',
    'DESCRIPTION': 'Milliy Sertifikat EdTech super-app REST API (Django 5.2 + DRF).',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
    'SCHEMA_PATH_PREFIX': r'/api/v[0-9]+',
    'SERVERS': [
        {'url': 'http://localhost:8001', 'description': 'Dev'},
        *(
            [{'url': os.environ['API_PUBLIC_URL'], 'description': 'Production'}]
            if os.getenv('API_PUBLIC_URL')
            else []
        ),
    ],
    'TAGS': [
        {'name': 'auth', 'description': 'Authentication (email, OTP, OAuth, Telegram)'},
        {'name': 'exams', 'description': 'Mock exams + practice + attempts'},
        {
            'name': 'leaderboard',
            'description': 'Leaderboards + engagement (streak, leagues, notifications)',
        },
        {'name': 'intelligence', 'description': 'AI tutor + skill mastery + open-ended grading'},
        {'name': 'commerce', 'description': 'Wallet + payments + subscriptions + affiliate'},
        {'name': 'analytics', 'description': 'B2B dashboard + reports'},
        {'name': 'gdpr', 'description': 'Data privacy (Article 17 erasure)'},
        {'name': 'health', 'description': 'Liveness/readiness probes'},
    ],
}

# ── CORS (django-cors-headers) ─────────────────────────────────
# Frontend (Next.js) lives on a separate origin in dev (localhost:3000) and
# prod (Vercel domain). JWT lives in an HttpOnly cookie, so the browser only
# sends it cross-origin when fetch uses `credentials: 'include'` AND the
# server replies with Access-Control-Allow-Origin = exact origin (not '*').
#
# CORS_ALLOWED_ORIGINS — dev.py and prod.py extend this list.
# CSRF_TRUSTED_ORIGINS — same origins, needed for any unsafe (POST) request.
CORS_ALLOWED_ORIGINS: list[str] = []
CORS_ALLOW_CREDENTIALS = True

# ── PostgreSQL Row Level Security (RLS) ────────────────────────────
# Multi-tenant isolation at database level (defense in depth)
ENABLE_RLS = os.getenv('ENABLE_RLS', 'true').lower() == 'true'

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_BOT_USERNAME = os.getenv('TELEGRAM_BOT_USERNAME', '')  # @ siz, masalan: milsert_bot
TELEGRAM_WEBHOOK_SECRET = os.getenv('TELEGRAM_WEBHOOK_SECRET', '')  # ixtiyoriy xavfsizlik token

# Next.js app origin (OAuth redirects, recovery links, anonymous home redirect).
FRONTEND_BASE_URL = os.getenv('FRONTEND_BASE_URL', 'http://localhost:3000').rstrip('/')

# ── django-allauth ────────────────────────────────────────────
ACCOUNT_ADAPTER = 'apps.accounts.adapters.AccountAdapter'
SOCIALACCOUNT_ADAPTER = 'apps.accounts.adapters.SocialAccountAdapter'
# Email required, no username field (allauth 65.x replacement for
# ACCOUNT_EMAIL_REQUIRED / ACCOUNT_USERNAME_REQUIRED / ACCOUNT_AUTHENTICATION_METHOD).
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
# 'optional': Google bypass (already verified), email/password requires verify
ACCOUNT_EMAIL_VERIFICATION = 'optional'
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USER_MODEL_USERNAME_FIELD = 'username'

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'APP': {
            'client_id': os.getenv('GOOGLE_CLIENT_ID', ''),
            'secret': os.getenv('GOOGLE_CLIENT_SECRET', ''),
            'key': '',
        },
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'OAUTH_PKCE_ENABLED': True,
        'VERIFIED_EMAIL': True,
    },
}

# GET /accounts/google/login/ starts the flow (a plain link from the Next.js UI).
SOCIALACCOUNT_LOGIN_ON_GET = True

# allauth login/signup dan keyin JWT cookie o'rnatiladigan URL
ACCOUNT_LOGIN_REDIRECT_URL = FRONTEND_BASE_URL + '/'
ACCOUNT_LOGOUT_REDIRECT_URL = FRONTEND_BASE_URL + '/login'
LOGIN_URL = FRONTEND_BASE_URL + '/login'

GOOGLE_OAUTH_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')

# ── SMS / OTP ─────────────────────────────────────────────────
# 'console' — dev (terminalga chiqaradi)
# 'playmobile' — production
# 'dummy' — test
# 'console' dev, 'playmobile' yoki 'eskiz' prod — admin tanlaydi
# SMS OTP sign-in is a feature flag, off by default (Telegram/password/Google are the launch methods).
ENABLE_SMS_OTP = os.getenv('ENABLE_SMS_OTP', 'false').lower() == 'true'
SMS_BACKEND = os.getenv('SMS_BACKEND', 'console')
PLAYMOBILE_LOGIN = os.getenv('PLAYMOBILE_LOGIN', '')
PLAYMOBILE_PASSWORD = os.getenv('PLAYMOBILE_PASSWORD', '')
PLAYMOBILE_ORIGINATOR = os.getenv('PLAYMOBILE_ORIGINATOR', 'MilSert')
ESKIZ_EMAIL = os.getenv('ESKIZ_EMAIL', '')
ESKIZ_PASSWORD = os.getenv('ESKIZ_PASSWORD', '')
ESKIZ_SENDER = os.getenv('ESKIZ_SENDER', '4546')

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'
ASGI_APPLICATION = 'core.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'yuzdanyuz_db'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', '127.0.0.1'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '60')),
        'CONN_HEALTH_CHECKS': True,
    }
}

AUTH_USER_MODEL = 'accounts.CustomUser'

# Single source of truth for password rules (register, change/set, recovery, admin).
# Policy: 8-128 chars, <=72 UTF-8 bytes, not one repeated char, not common/numeric,
# no platform words or 123456 variants, not similar to / equal to the username.
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {'NAME': 'apps.accounts.password_validators.MaximumLengthValidator'},
    {'NAME': 'apps.accounts.password_validators.Utf8ByteLengthValidator'},
    {'NAME': 'apps.accounts.password_validators.RepeatedCharacterValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
    {'NAME': 'apps.accounts.password_validators.LocalWordsValidator'},
    {'NAME': 'apps.accounts.password_validators.NotEqualToUsernameValidator'},
]

LANGUAGE_CODE = 'uz'
TIME_ZONE = 'Asia/Tashkent'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

PROJECT_BRAND_NAME = os.getenv('PROJECT_BRAND_NAME', 'Milliy Sertifikat')

# Version of the terms/privacy text shown at signup; stored with the consent timestamp.
TERMS_VERSION = os.getenv('TERMS_VERSION', '2026-11-12')

# ── Logging (ISSUE-X06) ───────────────────────────────────────
# Dev/test'da plain console handler — JSON formatter prod.py'da
# json_logging_dict() bilan o'rnatiladi. yuzdanyuz.events logger har ikkala
# muhitda mavjud bo'lishi shart, aks holda core.logging.log_event() chiqishi
# yo'qoladi.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'plain': {
            'format': '%(asctime)s %(levelname)s %(name)s %(message)s',
        },
        'json': {
            '()': 'pythonjsonlogger.json.JsonFormatter',
            'format': '%(asctime)s %(name)s %(levelname)s %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'plain',
        },
        'json_console': {
            'class': 'logging.StreamHandler',
            'formatter': 'json',
        },
    },
    'loggers': {
        # ISSUE-X06: structured events — JSON handler so aggregators index
        # fields as top-level keys (event, user_id, org_id, …).
        'yuzdanyuz.events': {
            'handlers': ['json_console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
    'root': {
        'handlers': ['console'],
        'level': os.getenv('LOG_LEVEL', 'INFO'),
    },
}

# ── Feature Flags ─────────────────────────────────────────────
# AI parser yoqilsa → tuzilmagan matn bloklari AI ga yuboriladi (narxi bor).
# O'chirilsa → faqat regex parser ishlaydi (bepul, lekin murakkab savollarni topa olmaydi).
ENABLE_AI_PARSER = os.getenv('ENABLE_AI_PARSER', 'true').lower() == 'true'

# Payments: real Payme/Click mode. When off, stub mode is only honoured under DEBUG or
# PAYMENTS_STUB_ALLOWED (test settings); otherwise payment endpoints return 503.
ENABLE_REAL_PAYMENTS = os.getenv('ENABLE_REAL_PAYMENTS', 'false').lower() == 'true'
PAYME_SECRET_KEY = os.getenv('PAYME_SECRET_KEY') or os.getenv('PAYME_MERCHANT_KEY', '')
CLICK_SECRET_KEY = os.getenv('CLICK_SECRET_KEY', '')

# ISSUE-505: django-flags — runtime feature gating (per-user, per-org, percentage rollout).
# Default conditions env var bilan boshqariladi. Admin'da `/admin/flags/` orqali override.
#
# Usage:
#   from flags.state import flag_enabled
#   if flag_enabled('ENABLE_REAL_PAYMENTS', request=request):
#       ... real charge ...
FLAGS = {
    # Existing env-based booleans wrapped as flags (no behavior change, just a layer of indirection)
    'ENABLE_AI_PARSER': [
        {'condition': 'boolean', 'value': ENABLE_AI_PARSER},
    ],
    'ENABLE_REAL_PAYMENTS': [
        {'condition': 'boolean', 'value': ENABLE_REAL_PAYMENTS},
    ],
    # ISSUE-505 demo: A/B test placeholder (off by default; flip on per-user/percentage in admin)
    'EXPERIMENTAL_NEW_DASHBOARD': [
        {'condition': 'boolean', 'value': False},
    ],
}

# Anthropic AI — Task 3 bulk import parser + Task 6 AI tutor + open-ended grading
# LLM_BACKEND: 'claude_cli' = local Claude subscription via `claude -p` (core/claude_cli.py),
# the main AI for now; 'anthropic_api' = Messages API with ANTHROPIC_API_KEY.
LLM_BACKEND = os.getenv('LLM_BACKEND', 'claude_cli')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
ANTHROPIC_MODEL = os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-5-5')
CLAUDE_CLI_PATH = os.getenv('CLAUDE_CLI_PATH', '')  # empty -> `claude` on PATH
CLAUDE_CLI_TIMEOUT = int(os.getenv('CLAUDE_CLI_TIMEOUT', '300'))
# YYYY-MM-DD: `claude setup-token` date + 1 year. Not secret. Drives the system check
# (apps/intelligence/checks.py) and the daily intelligence.check_ai_token_expiry task.
CLAUDE_CODE_OAUTH_TOKEN_EXPIRES = os.getenv('CLAUDE_CODE_OAUTH_TOKEN_EXPIRES', '')

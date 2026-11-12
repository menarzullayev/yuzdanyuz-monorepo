"""
Test settings — PostgreSQL, fast hashers, RLS support
"""

import os

from .base import *

SECRET_KEY = 'test-secret-key-not-for-production'
DEBUG = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('TEST_DB_NAME', 'yuzdanyuz_test'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', '127.0.0.1'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'CONN_MAX_AGE': 0,  # Don't persist connections in tests
    }
}

# Celery — synchronous, no async
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Redis — test instance
REDIS_URL = 'redis://127.0.0.1:6379/15'

# Channels — in-memory backend for tests (Redis siz)
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

# Per-test isolation: xdist workers must not share throttle/allauth cache state.
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

# pytest-django forces DEBUG=False and tests never run collectstatic, so the
# manifest storage would raise on every {% static %}.
STORAGES = {
    **STORAGES,
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

# Fast password hashing
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

# Keep migrations for testing (needed for RLS on PostgreSQL)
# class DisableMigrations:
#     def __contains__(self, item):
#         return True
#
#     def __getitem__(self, item):
#         return None
#
# MIGRATION_MODULES = DisableMigrations()

# Payments — pytest-django forces DEBUG=False, so allow the stub provider explicitly.
PAYMENTS_STUB_ALLOWED = True

# Email — in-memory
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

# Logging — suppress during tests
LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
}

# Debug toolbar — disabled in tests
DEBUG_TOOLBAR_CONFIG = {
    'SHOW_TOOLBAR_CALLBACK': lambda r: False,
}

# ── ISSUE-101: N+1 detection ──────────────────────────────────
# nplusone library installed (development.txt) ammo middleware test'da yoqilmaydi —
# uning `lazy_load` signal'i har lazy QuerySet'da fire bo'ladi va false
# positive ko'p. Buning o'rniga `assert_max_queries(N)` fixture (conftest.py)
# har test uchun aniq query budget bilan ishlaydi.
# Developer'lar lokal'da diagnostika uchun nplusone middleware'ni qo'lda
# yoqishi mumkin: NPLUSONE_LOG_LEVEL=WARNING runserver paytida.

# ── Auth Settings (Task 2 tests) ──────────────────────────────

# Telegram
TELEGRAM_BOT_TOKEN = 'test-bot-token-1234567890'
TELEGRAM_BOT_USERNAME = 'test_bot'
TELEGRAM_WEBHOOK_SECRET = 'test-webhook-secret'

# SMS OTP flag is off by default; the OTP code paths are exercised with it on.
ENABLE_SMS_OTP = True

# SMS Backend
SMS_BACKEND = 'console'  # Log to console in tests
PLAYMOBILE_LOGIN = 'test-login'
PLAYMOBILE_PASSWORD = 'test-password'

# Google OAuth
GOOGLE_CLIENT_ID = 'test-client-id.apps.googleusercontent.com'
GOOGLE_CLIENT_SECRET = 'test-client-secret'

# AI: never spawn a real `claude -p` from tests; tests that cover it set LLM_BACKEND themselves.
LLM_BACKEND = 'anthropic_api'

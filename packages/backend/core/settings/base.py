import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'not-secret-key-for-testing-only')

INSTALLED_APPS = [
    'django.contrib.admin',
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
    'allauth.socialaccount.providers.yandex',
    'allauth.socialaccount.providers.apple',
    # Celery infrastructure (admin'da beat schedule + result tracking)
    'django_celery_beat',
    'django_celery_results',
    # DRF — REST API
    'rest_framework',
    # Local apps — Domain-Driven Design
    'apps.accounts',  # User, auth, device session
    'apps.organizations',  # Organization (tenant), membership, roles
    'apps.catalog',  # Question, QuestionBank, Tag, AnswerChoice
    'apps.exams',  # MockExam, PracticeSession, UserAnswer, AntiCheat
    'apps.intelligence',  # KnowledgeGraph, SkillTag, StudyPlan, AIFeedback
    'apps.commerce',  # Wallet, Transaction, Subscription, Affiliate
    'apps.engagement',  # Streak, League, Badge, Notification
    'apps.analytics',  # ClickHouseEvent, Report, B2BDashboard
]

MIDDLEWARE = [
    'core.middleware.fix_prefix.ForceScriptNameMiddleware',
    'django.middleware.security.SecurityMiddleware',
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
    'core.middleware.rls_middleware.RLSMiddleware',  # PostgreSQL RLS setup (DB-level isolation)
    'core.middleware.rate_limit.RateLimitMiddleware',  # Redis-backed rate limiting (HTTP 429)
]

AUTHENTICATION_BACKENDS = [
    'apps.accounts.backends.JWTBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
    'django.contrib.auth.backends.ModelBackend',
]

SITE_ID = 1

REDIS_URL = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/1')

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
    'EXCEPTION_HANDLER': 'rest_framework.views.exception_handler',
}

# ── PostgreSQL Row Level Security (RLS) ────────────────────────────
# Multi-tenant isolation at database level (defense in depth)
ENABLE_RLS = os.getenv('ENABLE_RLS', 'true').lower() == 'true'

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_BOT_USERNAME = os.getenv('TELEGRAM_BOT_USERNAME', '')  # @ siz, masalan: milsert_bot
TELEGRAM_WEBHOOK_SECRET = os.getenv('TELEGRAM_WEBHOOK_SECRET', '')  # ixtiyoriy xavfsizlik token

# ── django-allauth ────────────────────────────────────────────
ACCOUNT_ADAPTER = 'apps.accounts.adapters.AccountAdapter'
SOCIALACCOUNT_ADAPTER = 'apps.accounts.adapters.SocialAccountAdapter'
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_USERNAME_REQUIRED = False
# 'optional': Google bypass (already verified), email/password requires verify
ACCOUNT_EMAIL_VERIFICATION = 'optional'
ACCOUNT_AUTHENTICATION_METHOD = 'email'
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
    'yandex': {
        'APP': {
            'client_id': os.getenv('YANDEX_CLIENT_ID', ''),
            'secret': os.getenv('YANDEX_CLIENT_SECRET', ''),
            'key': '',
        },
        'SCOPE': ['login:email', 'login:info', 'login:avatar'],
    },
    'apple': {
        'APP': {
            'client_id': os.getenv('APPLE_CLIENT_ID', ''),
            'secret': os.getenv('APPLE_SECRET', ''),
            'key': os.getenv('APPLE_PRIVATE_KEY', ''),
        },
        'VERIFIED_EMAIL': True,
    },
}

YANDEX_CLIENT_ID = os.getenv('YANDEX_CLIENT_ID', '')
YANDEX_CLIENT_SECRET = os.getenv('YANDEX_CLIENT_SECRET', '')
APPLE_CLIENT_ID = os.getenv('APPLE_CLIENT_ID', '')
APPLE_SECRET = os.getenv('APPLE_SECRET', '')
# allauth login/signup dan keyin JWT cookie o'rnatiladigan URL
ACCOUNT_LOGIN_REDIRECT_URL = 'accounts:login'
ACCOUNT_LOGOUT_REDIRECT_URL = 'accounts:login'

GOOGLE_OAUTH_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')

# ── SMS / OTP ─────────────────────────────────────────────────
# 'console' — dev (terminalga chiqaradi)
# 'playmobile' — production
# 'dummy' — test
# 'console' dev, 'playmobile' yoki 'eskiz' prod — admin tanlaydi
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
    }
}

AUTH_USER_MODEL = 'accounts.CustomUser'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
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

# ── Feature Flags ─────────────────────────────────────────────
# AI parser yoqilsa → tuzilmagan matn bloklari AI ga yuboriladi (narxi bor).
# O'chirilsa → faqat regex parser ishlaydi (bepul, lekin murakkab savollarni topa olmaydi).
ENABLE_AI_PARSER = os.getenv('ENABLE_AI_PARSER', 'true').lower() == 'true'

from django.core.exceptions import ImproperlyConfigured

from core.observability import init_sentry, json_logging_dict

from .base import *

DEBUG = False

# Sentry — DSN env-driven; safe no-op if SENTRY_DSN missing.
init_sentry()

# Production'da SECRET_KEY env'dan kelishi shart — base.py'dagi insecure default
# prod muhitida ishlasa, JWT/session imzolari aniq qiymat bilan yaratiladi va
# loyihani ekspozitsiya qiladi. Fail-loud o'rnatish o'rniga sukut bilan o'tib ketmaslik.
if not os.environ.get('SECRET_KEY'):
    raise ImproperlyConfigured(
        'SECRET_KEY environment variable is required in production. '
        'See .env.example for the expected format.'
    )
SECRET_KEY = os.environ['SECRET_KEY']

# Hardcoded ALLOWED_HOSTS — security-first (env override only if explicitly set)
ALLOWED_HOSTS = ['hsm.sammu.uz', 'srvr1.sammu.uz', '127.0.0.1', 'localhost']
_extra_hosts = os.getenv('ALLOWED_HOSTS', '').strip()
if _extra_hosts:
    ALLOWED_HOSTS = list(
        set(ALLOWED_HOSTS) | {h.strip() for h in _extra_hosts.split(',') if h.strip()}
    )

# CSRF — production domain ishlatadi (subpath bilan ham bir xil)
CSRF_TRUSTED_ORIGINS = ['https://hsm.sammu.uz']
USE_X_FORWARDED_HOST = True

# HTTPS xavfsizlik headerlari
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# Subpath deployment — FORCE_SCRIPT_NAME env-var support.
# Sets URL prefix, STATIC_URL, LOGIN URLs, AND cookie paths (scope to subpath
# so Yii2 host site's cookies don't conflict with Django).
_script_name = os.getenv('FORCE_SCRIPT_NAME', '').rstrip('/')
if _script_name:
    FORCE_SCRIPT_NAME = _script_name
    STATIC_URL = f'{_script_name}/static/'
    MEDIA_URL = f'{_script_name}/media/'
    LOGIN_REDIRECT_URL = f'{_script_name}/'
    LOGIN_URL = f'{_script_name}/accounts/login/'
    LOGOUT_REDIRECT_URL = f'{_script_name}/'
    # Cookie scoping — without this, /yuzdanyuz/ cookies would set path=/
    # and conflict with Yii2 cookies on the same domain.
    SESSION_COOKIE_PATH = f'{_script_name}/'
    CSRF_COOKIE_PATH = f'{_script_name}/'
    LANGUAGE_COOKIE_PATH = f'{_script_name}/'

# Production email
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'noreply@milliysertifikat.uz')

# Production logging — JSON via python-json-logger (see core.observability).
LOGGING = json_logging_dict(level=os.getenv('LOG_LEVEL', 'INFO'))

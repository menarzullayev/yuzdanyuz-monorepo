from django.core.exceptions import ImproperlyConfigured

from .base import *

DEBUG = False

# Production'da SECRET_KEY env'dan kelishi shart — base.py'dagi insecure default
# prod muhitida ishlasa, JWT/session imzolari aniq qiymat bilan yaratiladi va
# loyihani ekspozitsiya qiladi. Fail-loud o'rnatish o'rniga sukut bilan o'tib ketmaslik.
if not os.environ.get('SECRET_KEY'):
    raise ImproperlyConfigured(
        'SECRET_KEY environment variable is required in production. '
        'See .env.example for the expected format.'
    )
SECRET_KEY = os.environ['SECRET_KEY']

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '').split(',')

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

# Production email
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'noreply@milliysertifikat.uz')

# Production logging — Sentry (Task 10 da to'liq ulanadi)
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'json': {
            'format': '{"time":"%(asctime)s","level":"%(levelname)s","module":"%(module)s","message":"%(message)s"}',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'json',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': os.getenv('LOG_LEVEL', 'INFO'),
    },
}

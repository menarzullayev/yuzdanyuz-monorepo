import os

from .base import *

DEBUG = True

ALLOWED_HOSTS = ['*']

# Dev uchun oddiy console email backend
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Django Debug Toolbar (o'rnatilsa avtomatik ishlaydi)
try:
    import debug_toolbar  # noqa: F401

    INSTALLED_APPS += ['debug_toolbar']
    MIDDLEWARE.insert(0, 'debug_toolbar.middleware.DebugToolbarMiddleware')
    INTERNAL_IPS = ['127.0.0.1']
except ImportError:
    pass

# Dev da HTTPS talab qilinmaydi
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Next.js frontend dev server (localhost:3000) ham CSRF post qila olishi shart.
# Reverse proxy orqali ishlaganda uning origin'i CSRF_TRUSTED_ORIGINS env'ga yoziladi.
CSRF_TRUSTED_ORIGINS = [
    'http://localhost:3000',
    'http://127.0.0.1:3000',
    *[o.strip() for o in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if o.strip()],
]
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# CORS — frontend Next.js dev server. Cookie auth talab qiladi
# credentials:'include' + aniq origin (wildcard ishlamaydi).
CORS_ALLOWED_ORIGINS = [
    'http://localhost:3000',
    'http://127.0.0.1:3000',
]
# In dev only — wildcard'larda boshqa portga ham ruxsat (mobile RN expo, vite).
CORS_ALLOWED_ORIGIN_REGEXES = [
    r'^http://localhost:\d+$',
    r'^http://127\.0\.0\.1:\d+$',
]

_script_name = os.getenv('FORCE_SCRIPT_NAME', '').rstrip('/')
if _script_name:
    FORCE_SCRIPT_NAME = _script_name
    STATIC_URL = f'{_script_name}/static/'
    LOGIN_REDIRECT_URL = f'{_script_name}/'
    LOGIN_URL = f'{_script_name}/login/'

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

# PHP proxy orqali https://hsm.sammu.uz/yuzdanyuz/ da ishlash uchun
CSRF_TRUSTED_ORIGINS = ['https://hsm.sammu.uz']
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
FORCE_SCRIPT_NAME = '/yuzdanyuz'
STATIC_URL = '/yuzdanyuz/static/'
LOGIN_REDIRECT_URL = '/yuzdanyuz/'
LOGIN_URL = '/yuzdanyuz/login/'

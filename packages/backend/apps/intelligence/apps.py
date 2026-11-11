from django.apps import AppConfig


class IntelligenceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.intelligence'

    def ready(self):
        from . import checks, signals  # noqa: F401  (token expiry check, mistake notebook hook)

from django.apps import AppConfig


class IntelligenceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.intelligence'

    def ready(self):
        from . import checks  # noqa: F401  (registers the Claude token expiry check)

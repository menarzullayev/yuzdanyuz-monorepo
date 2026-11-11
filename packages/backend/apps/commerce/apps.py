from django.apps import AppConfig


class CommerceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.commerce'

    def ready(self):
        from . import checks  # noqa: F401  (registers system checks)

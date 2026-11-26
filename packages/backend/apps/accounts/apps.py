from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.accounts'

    def ready(self):
        import apps.accounts.checks  # noqa: F401
        import apps.accounts.signals  # noqa: F401
        import core.schema  # noqa: F401  (registers the OpenAPI cookie-auth extensions)

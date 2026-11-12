from django.apps import AppConfig


class OrganizationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.organizations'

    def ready(self):
        from core import db_rls

        from . import signals  # noqa: F401

        # RLS org variable rides on every statement inside a tenant context (E05)
        db_rls.connect_signal()

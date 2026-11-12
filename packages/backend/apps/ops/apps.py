from django.apps import AppConfig


class OpsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.ops'
    label = 'ops'
    verbose_name = 'Operations'

    def ready(self):
        from django.db.models.signals import post_migrate

        import core.checks  # noqa: F401
        from core import db_roles

        post_migrate.connect(db_roles.post_migrate_grants, dispatch_uid='db_runtime_grants')

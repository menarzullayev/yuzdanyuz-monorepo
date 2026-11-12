from django.apps import AppConfig


class OpsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.ops'
    label = 'ops'
    verbose_name = 'Operations'

    def ready(self):
        from django.core.signals import request_finished
        from django.db.backends.signals import connection_created
        from django.db.models.signals import post_migrate

        import core.checks  # noqa: F401
        from core import db_roles

        connection_created.connect(
            db_roles.on_connection_created, dispatch_uid='rls_bypass_default'
        )
        post_migrate.connect(db_roles.post_migrate_grants, dispatch_uid='db_runtime_grants')
        request_finished.connect(db_roles.reset_org_after_request, dispatch_uid='rls_reset_org')

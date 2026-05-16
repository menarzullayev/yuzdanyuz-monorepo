from django.apps import AppConfig


class WebhooksConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.webhooks'
    verbose_name = 'B2B Webhooks'

    def ready(self):
        # Connect post_save receiver on ExamAttempt → fires 'exam.submitted' webhook.
        from . import signals  # noqa: F401

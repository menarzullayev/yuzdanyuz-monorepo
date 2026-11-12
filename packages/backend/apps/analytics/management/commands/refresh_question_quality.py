"""Run the question quality refresh on demand (same code path as the Celery task)."""

from django.core.management.base import BaseCommand

from apps.analytics.tasks import refresh_question_quality


class Command(BaseCommand):
    help = 'Recompute question quality statistics (optionally for one organization).'

    def add_arguments(self, parser):
        parser.add_argument('--org', dest='org_id', default=None, help='Organization UUID')

    def handle(self, *args, **options):
        result = refresh_question_quality(options['org_id'])
        self.stdout.write(
            f'orgs={result["orgs"]} versions={result["versions"]} flagged={result["flagged"]}'
        )

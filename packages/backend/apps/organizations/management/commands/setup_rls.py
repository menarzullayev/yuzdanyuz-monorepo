"""
python manage.py setup_rls

Production da PostgreSQL RLS policies ni o'rnatadi.
Faqat bir marta ishga tushiriladi (yoki idempotent — OR REPLACE ishlatilgan).
"""

from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import connection

SQL_FILE = Path(__file__).resolve().parents[3] / 'sql' / 'rls_policies.sql'


class Command(BaseCommand):
    help = "PostgreSQL Row Level Security policies ni o'rnatadi (Task 10 / Production)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="SQL ni ekranda ko'rsatadi, lekin bajarmasdan",
        )

    def handle(self, *args, **options):
        sql = SQL_FILE.read_text()

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('--- DRY RUN ---'))
            self.stdout.write(sql)
            return

        with connection.cursor() as cursor:
            cursor.execute(sql)

        self.stdout.write(self.style.SUCCESS("RLS policies muvaffaqiyatli o'rnatildi."))

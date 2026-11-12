"""E01: indexes for question search and the hot Question filter.

`search_questions()` runs `content__icontains`, which Django compiles to
`UPPER(content::text) LIKE UPPER('%q%')`. A leading wildcard can only use a
pg_trgm GIN index on that exact expression; without it every search is a
sequential scan of all question versions.

Both indexes are built CONCURRENTLY (no write lock on a live table), hence
atomic = False. pg_trgm is a trusted extension (PG 13+): the database owner can
create it without superuser.
"""

from django.contrib.postgres.operations import AddIndexConcurrently, TrigramExtension
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('catalog', '0011_aiproviderconfig_profiles_encryption'),
    ]

    operations = [
        TrigramExtension(),
        migrations.RunSQL(
            sql=(
                'CREATE INDEX CONCURRENTLY IF NOT EXISTS catalog_qv_content_trgm '
                'ON catalog_questionversion USING gin ((UPPER(content::text)) gin_trgm_ops);'
            ),
            reverse_sql='DROP INDEX CONCURRENTLY IF EXISTS catalog_qv_content_trgm;',
        ),
        AddIndexConcurrently(
            model_name='question',
            index=models.Index(
                fields=['organization', 'subject', 'is_active'], name='catalog_q_org_subj_active'
            ),
        ),
    ]

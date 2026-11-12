"""Re-encrypt every database secret with the primary FIELD_ENCRYPTION_KEYS key (C18).

Rotation: prepend the new key to FIELD_ENCRYPTION_KEYS on every pod, run this command,
then remove the old key. Plaintext leftovers are encrypted on the way. Values are never
printed; a row that no configured key can decrypt aborts the whole run (nothing written).
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from psycopg2 import sql

from core import crypto

# (table, primary key column, encrypted column)
ENCRYPTED_COLUMNS = [
    ('catalog_aiproviderconfig', 'id', 'api_key'),
    ('webhooks_webhookendpoint', 'id', 'secret'),
    ('ops_botsettings', 'id', 'token_enc'),
    ('ops_botsettings', 'id', 'webhook_secret_enc'),
]


class Command(BaseCommand):
    help = 'Re-encrypt stored secrets with the primary FIELD_ENCRYPTION_KEYS key.'

    def handle(self, *args, **options):
        total = 0
        with transaction.atomic(), connection.cursor() as cur:
            for table, pk, column in ENCRYPTED_COLUMNS:
                q = sql.SQL('SELECT {pk}, {col} FROM {t} WHERE {col} <> {empty}').format(
                    pk=sql.Identifier(pk),
                    col=sql.Identifier(column),
                    t=sql.Identifier(table),
                    empty=sql.Literal(''),
                )
                cur.execute(q)
                rows = cur.fetchall()
                for row_id, value in rows:
                    try:
                        new = crypto.rotate(value)
                    except crypto.DecryptionError as exc:
                        raise CommandError(f'{table}.{column} id={row_id}: {exc}') from exc
                    cur.execute(
                        sql.SQL('UPDATE {t} SET {col} = %s WHERE {pk} = %s').format(
                            t=sql.Identifier(table),
                            col=sql.Identifier(column),
                            pk=sql.Identifier(pk),
                        ),
                        [new, row_id],
                    )
                total += len(rows)
                self.stdout.write(f'{table}.{column}: {len(rows)} re-encrypted')
        self.stdout.write(self.style.SUCCESS(f'done: {total} values'))

"""Model fields for secrets encrypted at rest (core/crypto.py)."""

from django.db import models

from core import crypto


class EncryptedTextField(models.TextField):
    """Plaintext in Python, 'enc1:' + Fernet token in the database.

    Legacy plaintext rows (no prefix) read back unchanged and are encrypted on the next
    save; `manage.py rotate_field_encryption` re-encrypts every row with the primary key.
    Not usable in lookups: equal plaintexts encrypt to different tokens.
    """

    description = 'Text encrypted at rest'

    def from_db_value(self, value, expression, connection):
        return crypto.decrypt(value) if value else value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value or crypto.is_encrypted(value):
            return value
        return crypto.encrypt(value)

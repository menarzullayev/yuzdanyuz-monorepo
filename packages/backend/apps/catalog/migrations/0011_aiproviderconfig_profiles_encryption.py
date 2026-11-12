"""AIProviderConfig: task profiles (writing/speaking), extra options, encrypted API keys."""

from django.db import migrations, models


def encrypt_existing_keys(apps, schema_editor):
    from core import crypto

    Config = apps.get_model('catalog', 'AIProviderConfig')
    for row in Config.objects.exclude(api_key=''):
        if crypto.is_encrypted(row.api_key):
            continue
        row.api_key_hint = row.api_key[-4:]
        row.api_key = crypto.encrypt(row.api_key)
        row.save(update_fields=['api_key', 'api_key_hint'])


def decrypt_keys(apps, schema_editor):
    from core import crypto

    Config = apps.get_model('catalog', 'AIProviderConfig')
    for row in Config.objects.exclude(api_key=''):
        row.api_key = crypto.decrypt(row.api_key)
        row.save(update_fields=['api_key'])


class Migration(migrations.Migration):
    dependencies = [
        ('catalog', '0010_contenttarget_rls'),
    ]

    operations = [
        migrations.AddField(
            model_name='aiproviderconfig',
            name='api_key_hint',
            field=models.CharField(blank=True, editable=False, max_length=8),
        ),
        migrations.AddField(
            model_name='aiproviderconfig',
            name='extra_options',
            field=models.JSONField(blank=True, default=dict, verbose_name="Qo'shimcha sozlamalar"),
        ),
        migrations.AlterField(
            model_name='aiproviderconfig',
            name='api_key',
            field=models.CharField(
                blank=True,
                help_text="Ollama uchun bo'sh qoldiring. Shifrlangan holda saqlanadi.",
                max_length=1000,
                verbose_name='API kalit',
            ),
        ),
        migrations.AlterField(
            model_name='aiproviderconfig',
            name='use_for',
            field=models.CharField(
                choices=[
                    ('parser', 'Savol Parseri (Bulk Import)'),
                    ('tutor', 'AI Tutor (Diagnostika)'),
                    ('writing', 'Writing baholash'),
                    ('speaking', 'Speaking baholash'),
                    ('all', 'Barchasi (default profil)'),
                ],
                default='all',
                max_length=20,
                verbose_name='Foydalanish maqsadi',
            ),
        ),
        migrations.RunPython(encrypt_existing_keys, decrypt_keys),
    ]

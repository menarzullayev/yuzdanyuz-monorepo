import uuid

import django.db.models.deletion
from django.db import migrations, models

import core.managers


class Migration(migrations.Migration):
    dependencies = [
        ('catalog', '0008_aiproviderconfig_claude_cli'),
        ('organizations', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ContentTarget',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'id',
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ('target_questions', models.PositiveIntegerField(default=300)),
                ('target_variants', models.PositiveIntegerField(default=10)),
                (
                    'organization',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='%(app_label)s_%(class)s_set',
                        to='organizations.organization',
                        verbose_name='Tashkilot',
                    ),
                ),
                (
                    'subject',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='content_targets',
                        to='catalog.subject',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Kontent maqsadi',
                'verbose_name_plural': 'Kontent maqsadlari',
                'constraints': [
                    models.UniqueConstraint(
                        fields=('organization', 'subject'), name='uniq_contenttarget_org_subject'
                    )
                ],
            },
        ),
    ]

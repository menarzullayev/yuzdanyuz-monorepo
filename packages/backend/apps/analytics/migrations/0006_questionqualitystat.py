import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('analytics', '0005_failedtask'),
        ('catalog', '0008_aiproviderconfig_claude_cli'),
        ('organizations', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='QuestionQualityStat',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'id',
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('correct_count', models.PositiveIntegerField(default=0)),
                ('skipped_count', models.PositiveIntegerField(default=0)),
                ('correct_rate', models.FloatField(default=0.0)),
                ('skip_rate', models.FloatField(default=0.0)),
                ('flags', models.JSONField(blank=True, default=list)),
                ('is_preliminary', models.BooleanField(db_index=True, default=True)),
                ('computed_at', models.DateTimeField()),
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
                    'question_version',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='quality_stats',
                        to='catalog.questionversion',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Question Quality Stat',
                'verbose_name_plural': 'Question Quality Stats',
                'ordering': ['-attempts'],
                'indexes': [
                    models.Index(
                        fields=['organization', 'is_preliminary'],
                        name='analytics_q_organiz_24a55f_idx',
                    )
                ],
                'constraints': [
                    models.UniqueConstraint(
                        fields=('organization', 'question_version'),
                        name='uniq_qquality_org_version',
                    )
                ],
            },
        ),
    ]

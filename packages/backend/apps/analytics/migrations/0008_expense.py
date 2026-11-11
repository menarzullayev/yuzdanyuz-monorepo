import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('analytics', '0007_questionqualitystat_rls'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Expense',
            fields=[
                ('is_deleted', models.BooleanField(db_index=True, default=False)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('pii_redacted', models.BooleanField(default=False)),
                (
                    'id',
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ('date', models.DateField(db_index=True)),
                ('amount_uzs', models.DecimalField(decimal_places=2, max_digits=14)),
                (
                    'type',
                    models.CharField(
                        choices=[
                            ('server', 'Server / hosting'),
                            ('ai', 'AI services'),
                            ('payment_fees', "To'lov komissiyalari"),
                            ('advertising', 'Reklama'),
                            ('salaries', 'Ish haqi'),
                            ('content', 'Kontent'),
                            ('other', 'Boshqa'),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ('note', models.CharField(blank=True, max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'created_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'verbose_name': 'Expense',
                'verbose_name_plural': 'Expenses',
                'ordering': ['-date', '-created_at'],
                'constraints': [
                    models.CheckConstraint(
                        condition=models.Q(('amount_uzs__gt', 0)), name='expense_amount_gt_0'
                    )
                ],
            },
        ),
    ]

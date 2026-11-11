import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0008_aiproviderconfig_claude_cli'),
        ('exams', '0007_add_delete_policies'),
        ('intelligence', '0003_issue_103b_fk_set_null'),
        ('organizations', '0003_historicalmembership_historicalorganization_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='MistakeCard',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('stage', models.PositiveSmallIntegerField(default=1)),
                ('due_at', models.DateTimeField()),
                ('last_reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('lapses', models.PositiveIntegerField(default=0)),
                ('mastered_at', models.DateTimeField(blank=True, null=True)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='organizations.organization', verbose_name='Tashkilot')),
                ('question', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mistake_cards', to='catalog.question')),
                ('source_answer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='exams.useranswer')),
                ('source_attempt', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='exams.examattempt')),
                ('source_session', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='exams.practicesession')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mistake_cards', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Mistake Card',
                'verbose_name_plural': 'Mistake Cards',
                'ordering': ['due_at'],
                'indexes': [models.Index(fields=['user', 'mastered_at', 'due_at'], name='intelligenc_user_id_99dd87_idx')],
                'constraints': [
                    models.UniqueConstraint(fields=('user', 'question'), name='mistakecard_user_question_uniq'),
                    models.CheckConstraint(condition=models.Q(('stage__gte', 1), ('stage__lte', 4)), name='mistakecard_stage_1_4'),
                ],
            },
        ),
    ]

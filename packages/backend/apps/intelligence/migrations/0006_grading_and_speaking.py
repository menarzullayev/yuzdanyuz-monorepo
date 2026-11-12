from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('intelligence', '0005_mistakecard_rls'),
    ]

    operations = [
        migrations.AddField(
            model_name='openendedsubmission',
            name='human_criteria',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name='openendedsubmission',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'AI tahlil kutilmoqda'),
                    ('ai_reviewed', 'AI baholangan'),
                    ('human_approved', 'Inson tasdiqlagan'),
                    ('disputed', "Bahsli (admin ko'radi)"),
                    ('failed', 'AI xato'),
                    ('awaiting_human', "Inson baholashi kutilmoqda (AI o'tkazib yuborildi)"),
                ],
                db_index=True,
                default='pending',
                max_length=16,
            ),
        ),
    ]

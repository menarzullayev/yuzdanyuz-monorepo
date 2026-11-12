from django.db import migrations, models

CHOICES = [
    ('manual', "Admin qo'shdi"),
    ('invite', 'Invite link'),
    ('api', 'API integratsiya'),
    ('auto', 'Avtomatik (platforma)'),
]


class Migration(migrations.Migration):
    dependencies = [('organizations', '0006_b2b_role_permissions')]

    operations = [
        migrations.AlterField(
            model_name=name,
            name='joined_via',
            field=models.CharField(choices=CHOICES, default='manual', max_length=20),
        )
        for name in ('membership', 'historicalmembership')
    ]

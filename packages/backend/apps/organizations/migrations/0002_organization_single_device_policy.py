# Generated migration for single_device_policy field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='organization',
            name='single_device_policy',
            field=models.BooleanField(
                default=True,
                help_text='True: 1 ta qurilma faol, yangi kirsa eski sessiya uzilib qoladi',
                verbose_name='Bir qurilma siyosati'
            ),
        ),
    ]

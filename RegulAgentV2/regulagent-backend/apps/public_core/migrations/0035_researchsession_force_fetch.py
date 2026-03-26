from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('public_core', '0034_welltimelineevent'),
    ]

    operations = [
        migrations.AddField(
            model_name='researchsession',
            name='force_fetch',
            field=models.BooleanField(default=False, help_text='If True, bypass document cache and re-fetch from source'),
        ),
    ]

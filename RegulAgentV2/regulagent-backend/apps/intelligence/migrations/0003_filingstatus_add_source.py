from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('intelligence', '0002_add_polling_beat_schedule'),
    ]

    operations = [
        migrations.AddField(
            model_name='filingstatusrecord',
            name='source',
            field=models.CharField(
                choices=[
                    ('synced', 'Synced from Portal'),
                    ('submitted', 'Submitted via Platform'),
                    ('manual', 'Manually Created'),
                ],
                db_index=True,
                default='manual',
                help_text='How this filing entered the system',
                max_length=20,
            ),
        ),
    ]

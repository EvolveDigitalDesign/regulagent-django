from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intelligence", "0003_filingstatus_add_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="portalcredential",
            name="key_salt",
            field=models.BinaryField(
                default=b"",
                help_text="Per-credential salt for key derivation",
            ),
        ),
    ]

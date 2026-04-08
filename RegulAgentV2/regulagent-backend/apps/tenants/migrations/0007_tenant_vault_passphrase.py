from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0006_clientworkspace_usagerecord_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="vault_passphrase_hash",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Hashed vault passphrase for credential management authorization",
                max_length=255,
            ),
            preserve_default=False,
        ),
    ]

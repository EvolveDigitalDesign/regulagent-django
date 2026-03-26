from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('public_core', '0032_documentsegment'),
    ]

    operations = [
        migrations.AddField(
            model_name='extracteddocument',
            name='segment',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='extractions',
                to='public_core.documentsegment',
            ),
        ),
    ]

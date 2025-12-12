# Generated migration to add tracking_no field to ExtractedDocument

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("public_core", "0007_add_w3_orm_models"),
    ]

    operations = [
        migrations.AddField(
            model_name='extracteddocument',
            name='tracking_no',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Tracking No. from W-2 form header (used for revision tracking and consolidation)",
                max_length=64,
                null=True
            ),
        ),
        migrations.AddIndex(
            model_name='extracteddocument',
            index=models.Index(
                fields=["api_number", "document_type", "tracking_no"],
                name="public_core_api_numb_document_tracking_idx"
            ),
        ),
        migrations.AddIndex(
            model_name='extracteddocument',
            index=models.Index(
                fields=["tracking_no", "document_type"],
                name="public_core_tracking_no_document_idx"
            ),
        ),
    ]




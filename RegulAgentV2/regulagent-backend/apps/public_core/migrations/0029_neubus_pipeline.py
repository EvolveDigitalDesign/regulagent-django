from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("public_core", "0028_wellcomponent_wellcomponentsnapshot"),
    ]

    operations = [
        # Task 1: Add lease_id to WellRegistry
        migrations.AddField(
            model_name="wellregistry",
            name="lease_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Neubus lease ID for TX wells",
                max_length=32,
            ),
            preserve_default=False,
        ),

        # Task 2A: Alter source_type to include 'neubus'
        migrations.AlterField(
            model_name="extracteddocument",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("rrc", "RRC - Public Regulator Data"),
                    ("tenant_upload", "Tenant Upload - User Provided"),
                    ("operator_packet", "Operator Packet - Approved Execution Plan"),
                    ("neubus", "Neubus - TX RRC Document Archive"),
                ],
                db_index=True,
                default="rrc",
                help_text="Origin of document: RRC public data or tenant upload",
                max_length=16,
            ),
        ),

        # Task 2B: Add neubus fields to ExtractedDocument
        migrations.AddField(
            model_name="extracteddocument",
            name="neubus_filename",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Original filename from Neubus archive",
                max_length=255,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="extracteddocument",
            name="source_page",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text="First page number of this form in the source document",
            ),
        ),
        migrations.AddField(
            model_name="extracteddocument",
            name="file_hash",
            field=models.CharField(
                blank=True,
                default="",
                help_text="SHA-256 hash of the source file",
                max_length=64,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="extracteddocument",
            name="form_group_index",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text="Nth form of this type in the document",
            ),
        ),

        # Task 2C: Add neubus_filename index to ExtractedDocument
        migrations.AddIndex(
            model_name="extracteddocument",
            index=models.Index(fields=["neubus_filename"], name="public_core_neubus__filename_idx"),
        ),

        # Task 3: Create NeubusLease model
        migrations.CreateModel(
            name="NeubusLease",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("lease_id", models.CharField(db_index=True, max_length=32, unique=True)),
                ("field_name", models.CharField(blank=True, max_length=128)),
                ("lease_name", models.CharField(blank=True, max_length=128)),
                ("operator", models.CharField(blank=True, max_length=128)),
                ("county", models.CharField(blank=True, max_length=64)),
                ("district", models.CharField(blank=True, max_length=8)),
                (
                    "neubus_record_ids",
                    models.JSONField(
                        default=list,
                        help_text="List of Neubus record IDs associated with this lease",
                    ),
                ),
                ("last_checked", models.DateField(blank=True, null=True)),
                (
                    "max_upload_date",
                    models.CharField(
                        blank=True,
                        help_text="Most recent upload date string from Neubus",
                        max_length=64,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "public_core_neubus_lease",
            },
        ),

        # Task 3: Create NeubusDocument model
        migrations.CreateModel(
            name="NeubusDocument",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "lease",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="documents",
                        to="public_core.neubuslease",
                    ),
                ),
                ("neubus_filename", models.CharField(db_index=True, max_length=255, unique=True)),
                ("well_number", models.CharField(blank=True, max_length=32)),
                ("api", models.CharField(blank=True, db_index=True, max_length=20)),
                ("pages", models.PositiveIntegerField(default=0)),
                (
                    "form_types_by_page",
                    models.JSONField(
                        default=dict,
                        help_text='Map of form type to page numbers, e.g. {"W3": [1], "W-15": [2,3]}',
                    ),
                ),
                (
                    "uploaded_on",
                    models.CharField(
                        blank=True,
                        help_text="Upload date string from Neubus",
                        max_length=64,
                    ),
                ),
                ("date_ingested", models.DateField(auto_now_add=True)),
                ("file_hash", models.CharField(blank=True, max_length=64)),
                (
                    "classification_status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("processing", "Processing"),
                            ("complete", "Complete"),
                            ("error", "Error"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                (
                    "extraction_status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("processing", "Processing"),
                            ("complete", "Complete"),
                            ("error", "Error"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                (
                    "local_path",
                    models.TextField(
                        blank=True,
                        help_text="Path to the file in cold storage",
                    ),
                ),
            ],
            options={
                "db_table": "public_core_neubus_document",
                "indexes": [
                    models.Index(fields=["api"], name="public_core_neubus_doc_api_idx"),
                    models.Index(fields=["classification_status"], name="public_core_neubus_doc_cls_idx"),
                    models.Index(fields=["extraction_status"], name="public_core_neubus_doc_ext_idx"),
                ],
            },
        ),
    ]

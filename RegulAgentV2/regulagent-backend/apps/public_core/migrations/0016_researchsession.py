import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("public_core", "0015_documentvector_hnsw_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="ResearchSession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("api_number", models.CharField(db_index=True, max_length=16)),
                (
                    "state",
                    models.CharField(
                        choices=[("NM", "New Mexico"), ("TX", "Texas"), ("UT", "Utah")],
                        default="TX",
                        max_length=2,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("fetching", "Fetching"),
                            ("indexing", "Indexing"),
                            ("ready", "Ready"),
                            ("error", "Error"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                (
                    "well",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="research_sessions",
                        to="public_core.wellregistry",
                    ),
                ),
                ("total_documents", models.PositiveIntegerField(default=0)),
                ("indexed_documents", models.PositiveIntegerField(default=0)),
                ("failed_documents", models.PositiveIntegerField(default=0)),
                ("error_message", models.TextField(blank=True, default="")),
                ("document_list", models.JSONField(blank=True, default=list)),
                ("celery_task_id", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="researchsession",
            index=models.Index(fields=["api_number", "state", "status"], name="public_core_api_num_state_status_idx"),
        ),
    ]

# Generated manually on 2026-04-07

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("public_core", "0040_w3a_source_audit"),
        ("tenants", "0006_clientworkspace_usagerecord_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ManualWBD",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "api14",
                    models.CharField(db_index=True, max_length=20),
                ),
                (
                    "diagram_type",
                    models.CharField(
                        choices=[
                            ("current", "Current Wellbore"),
                            ("planned", "Planned Plugging"),
                            ("as_plugged", "As-Plugged"),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                (
                    "title",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "diagram_data",
                    models.JSONField(
                        help_text="Complete diagram payload matching frontend renderer shape"
                    ),
                ),
                (
                    "tenant_id",
                    models.UUIDField(db_index=True),
                ),
                (
                    "is_archived",
                    models.BooleanField(default=False),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "well",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="manual_wbds",
                        to="public_core.wellregistry",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="manual_wbds",
                        to="tenants.clientworkspace",
                    ),
                ),
            ],
            options={
                "db_table": "public_core_manual_wbd",
                "ordering": ["-updated_at"],
                "indexes": [
                    models.Index(
                        fields=["api14", "tenant_id", "diagram_type"],
                        name="public_core_api14_te_diagty_idx",
                    ),
                    models.Index(
                        fields=["tenant_id"],
                        name="public_core_tenant_id_mwbd_idx",
                    ),
                ],
            },
        ),
    ]

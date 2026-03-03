# Generated manually for BE1-002: Safe tenant deletion with backups

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0004_planfeature_and_feature_overrides"),
    ]

    operations = [
        migrations.CreateModel(
            name="DeletedTenantBackup",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "tenant_id",
                    models.UUIDField(
                        db_index=True,
                        help_text="Original tenant UUID"
                    ),
                ),
                (
                    "tenant_slug",
                    models.CharField(
                        db_index=True,
                        max_length=64
                    ),
                ),
                (
                    "tenant_name",
                    models.CharField(max_length=255),
                ),
                (
                    "schema_name",
                    models.CharField(
                        help_text="PostgreSQL schema name",
                        max_length=63
                    ),
                ),
                (
                    "backup_path",
                    models.CharField(
                        help_text="Full path to pg_dump backup file",
                        max_length=512
                    ),
                ),
                (
                    "backup_size_bytes",
                    models.BigIntegerField(
                        blank=True,
                        help_text="Size of backup file in bytes",
                        null=True
                    ),
                ),
                (
                    "backup_checksum",
                    models.CharField(
                        blank=True,
                        help_text="SHA256 checksum of backup file",
                        max_length=64
                    ),
                ),
                (
                    "soft_deleted_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        help_text="When tenant was marked for deletion (soft delete)"
                    ),
                ),
                (
                    "hard_deleted_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When schema was actually dropped (hard delete)",
                        null=True
                    ),
                ),
                (
                    "scheduled_deletion_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When schema is scheduled to be dropped",
                        null=True
                    ),
                ),
                (
                    "backup_verified",
                    models.BooleanField(
                        default=False,
                        help_text="Whether backup integrity was verified"
                    ),
                ),
                (
                    "verification_message",
                    models.TextField(
                        blank=True,
                        help_text="Details from backup verification"
                    ),
                ),
                (
                    "deleted_by_email",
                    models.EmailField(
                        blank=True,
                        help_text="Email of user who initiated deletion"
                    ),
                ),
                (
                    "deletion_reason",
                    models.TextField(
                        blank=True,
                        help_text="Reason for deletion (audit trail)"
                    ),
                ),
                (
                    "metadata",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Additional metadata (record counts, etc.)"
                    ),
                ),
            ],
            options={
                "verbose_name": "Deleted Tenant Backup",
                "verbose_name_plural": "Deleted Tenant Backups",
                "ordering": ["-soft_deleted_at"],
            },
        ),
        migrations.AddIndex(
            model_name="deletedtenantbackup",
            index=models.Index(
                fields=["-soft_deleted_at"],
                name="tenants_del_soft_de_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="deletedtenantbackup",
            index=models.Index(
                fields=["scheduled_deletion_at"],
                name="tenants_del_schedul_idx"
            ),
        ),
    ]

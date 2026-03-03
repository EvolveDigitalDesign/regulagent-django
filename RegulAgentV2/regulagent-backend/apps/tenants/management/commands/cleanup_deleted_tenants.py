"""
Management command to clean up soft-deleted tenants that have passed their retention period.

Usage:
    python manage.py cleanup_deleted_tenants [--dry-run] [--force]

This should be run periodically (e.g., daily via cron or Celery Beat).
"""
import logging
from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.tenants.models import Tenant, DeletedTenantBackup

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Clean up soft-deleted tenants that have passed their retention period'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip confirmation prompts',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']

        self.stdout.write(self.style.WARNING(
            'Cleanup of soft-deleted tenants starting...'
        ))

        # Find DeletedTenantBackup records that are pending deletion
        now = timezone.now()
        pending_deletions = DeletedTenantBackup.objects.filter(
            hard_deleted_at__isnull=True,  # Not yet hard deleted
            scheduled_deletion_at__isnull=False,  # Has scheduled deletion
            scheduled_deletion_at__lte=now,  # Scheduled time has passed
        ).order_by('scheduled_deletion_at')

        if not pending_deletions.exists():
            self.stdout.write(self.style.SUCCESS(
                'No tenants pending hard deletion.'
            ))
            return

        self.stdout.write(
            f"Found {pending_deletions.count()} tenant(s) pending hard deletion:"
        )

        for backup_record in pending_deletions:
            self.stdout.write(
                f"\n  - {backup_record.tenant_slug} (schema: {backup_record.schema_name})"
                f"\n    Soft deleted: {backup_record.soft_deleted_at}"
                f"\n    Scheduled deletion: {backup_record.scheduled_deletion_at}"
                f"\n    Backup: {backup_record.backup_path}"
                f"\n    Backup verified: {backup_record.backup_verified}"
            )

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\n[DRY RUN] Would delete the above tenants. Run without --dry-run to proceed.'
            ))
            return

        # Confirm deletion
        if not force:
            confirm = input(
                f"\nProceed with hard deletion of {pending_deletions.count()} tenant(s)? "
                "This will permanently drop their PostgreSQL schemas. [y/N]: "
            )
            if confirm.lower() != 'y':
                self.stdout.write(self.style.WARNING('Deletion cancelled.'))
                return

        # Perform hard deletions
        deleted_count = 0
        error_count = 0

        for backup_record in pending_deletions:
            try:
                # Check if tenant still exists
                try:
                    tenant = Tenant.objects.get(id=backup_record.tenant_id)
                except Tenant.DoesNotExist:
                    # Tenant was already deleted outside this process
                    logger.warning(
                        f"Tenant {backup_record.tenant_slug} (ID: {backup_record.tenant_id}) "
                        "not found - may have been deleted externally"
                    )
                    # Mark as hard deleted anyway
                    backup_record.hard_deleted_at = timezone.now()
                    backup_record.save(update_fields=['hard_deleted_at'])
                    deleted_count += 1
                    self.stdout.write(self.style.WARNING(
                        f"  ✓ Marked {backup_record.tenant_slug} as deleted (tenant not found)"
                    ))
                    continue

                # Verify backup before deletion
                if not backup_record.backup_verified:
                    raise ValueError(
                        f"Backup not verified for tenant {backup_record.tenant_slug}. "
                        "Refusing to delete without verified backup."
                    )

                # Nullify public schema references
                from apps.public_core.models import PlanSnapshot
                plan_snapshots_updated = PlanSnapshot.objects.filter(
                    tenant_id=tenant.id
                ).update(tenant_id=None)

                logger.info(
                    f"Nullified {plan_snapshots_updated} PlanSnapshot references "
                    f"for tenant {backup_record.tenant_slug}"
                )

                # Delete tenant (drops schema)
                logger.info(f"Dropping schema: {tenant.schema_name}")
                tenant.delete(force_drop=True)

                # Update backup record
                backup_record.hard_deleted_at = timezone.now()
                backup_record.save(update_fields=['hard_deleted_at'])

                deleted_count += 1
                self.stdout.write(self.style.SUCCESS(
                    f"  ✓ Deleted {backup_record.tenant_slug} (schema: {backup_record.schema_name})"
                ))

            except Exception as e:
                error_count += 1
                logger.error(
                    f"Failed to delete tenant {backup_record.tenant_slug}: {e}",
                    exc_info=True
                )
                self.stdout.write(self.style.ERROR(
                    f"  ✗ Failed to delete {backup_record.tenant_slug}: {e}"
                ))

        # Summary
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS(
            f"Cleanup complete: {deleted_count} tenant(s) deleted, {error_count} error(s)"
        ))

        if error_count > 0:
            self.stdout.write(self.style.ERROR(
                f"\n⚠ {error_count} tenant(s) failed to delete. Check logs for details."
            ))

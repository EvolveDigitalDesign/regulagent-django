"""
Example script demonstrating safe tenant deletion with backups.

This script shows various ways to delete tenants using the new deletion system.
"""
from apps.tenants.models import Tenant, DeletedTenantBackup
from apps.tenants.utils import delete_tenant
from apps.tenants.services import PgDumpBackupService


def example_hard_delete_with_backup():
    """
    Example 1: Hard delete with automatic backup (default behavior)

    This immediately drops the tenant schema after creating and verifying a backup.
    """
    print("\n=== Example 1: Hard Delete with Backup ===")

    tenant = Tenant.objects.get(slug='acme')

    result = delete_tenant(
        tenant,
        deleted_by_email='admin@example.com',
        deletion_reason='Customer requested account deletion'
    )

    print(f"Tenant deleted: {result['tenant_deleted']}")
    print(f"Backup created: {result['backup_created']}")
    print(f"Backup path: {result['backup_path']}")
    print(f"Backup size: {result['backup_size_bytes']} bytes")
    print(f"Backup checksum: {result['backup_checksum']}")
    print(f"Backup verified: {result['backup_verified']}")
    print(f"Backup record ID: {result['backup_record_id']}")


def example_soft_delete_with_retention():
    """
    Example 2: Soft delete with 30-day retention period

    This marks the tenant for deletion but keeps the schema for 30 days.
    The schema will be dropped after the retention period by the cleanup command.
    """
    print("\n=== Example 2: Soft Delete with Retention ===")

    tenant = Tenant.objects.get(slug='beta-corp')

    result = delete_tenant(
        tenant,
        soft_delete=True,
        retention_days=30,
        deleted_by_email='admin@example.com',
        deletion_reason='Trial period expired - 30 day grace period'
    )

    print(f"Tenant deleted: {result['tenant_deleted']}")  # False
    print(f"Soft delete: {result['soft_delete']}")  # True
    print(f"Scheduled deletion: {result['scheduled_deletion_at']}")
    print(f"Backup path: {result['backup_path']}")
    print(f"Backup verified: {result['backup_verified']}")

    # Tenant schema still exists, can be recovered
    print("\nSchema still exists - can be recovered before scheduled deletion")


def example_custom_backup_location():
    """
    Example 3: Delete with custom backup directory

    Useful for storing backups on a separate volume or network storage.
    """
    print("\n=== Example 3: Custom Backup Location ===")

    tenant = Tenant.objects.get(slug='gamma-inc')

    result = delete_tenant(
        tenant,
        backup_dir='/mnt/secure_backups/tenants/',
        deleted_by_email='admin@example.com',
        deletion_reason='Company merged with competitor'
    )

    print(f"Backup path: {result['backup_path']}")
    print(f"Custom directory used: /mnt/secure_backups/tenants/")


def example_json_backup_legacy():
    """
    Example 4: Use legacy JSON backup instead of pg_dump

    For backward compatibility or when you need human-readable backups.
    """
    print("\n=== Example 4: Legacy JSON Backup ===")

    tenant = Tenant.objects.get(slug='delta-systems')

    result = delete_tenant(
        tenant,
        use_pg_dump=False,  # Use JSON serialization instead
        deleted_by_email='admin@example.com',
        deletion_reason='Testing JSON backup compatibility'
    )

    print(f"Backup path: {result['backup_path']}")  # .zip file
    print(f"Files listed: {result['files_listed']}")
    print("Backup is ZIP file with JSON data and manifest")


def example_verify_backup():
    """
    Example 5: Verify an existing backup

    Useful for auditing backups or before attempting restoration.
    """
    print("\n=== Example 5: Verify Existing Backup ===")

    backup_record = DeletedTenantBackup.objects.latest('soft_deleted_at')

    service = PgDumpBackupService()
    is_valid, message = service.verify_backup(backup_record.backup_path)

    print(f"Backup path: {backup_record.backup_path}")
    print(f"Valid: {is_valid}")
    print(f"Message: {message}")
    print(f"Checksum: {backup_record.backup_checksum}")


def example_restore_backup():
    """
    Example 6: Restore a backup to a new schema

    Demonstrates how to recover a deleted tenant.
    """
    print("\n=== Example 6: Restore Backup ===")

    # Get the backup record
    backup_record = DeletedTenantBackup.objects.get(tenant_slug='acme')

    # Create a new tenant for restoration
    restored_tenant = Tenant.objects.create(
        name=f"{backup_record.tenant_name} (Restored)",
        slug=f"{backup_record.tenant_slug}-restored",
        schema_name=f"{backup_record.schema_name}_restored"
    )

    print(f"Created new tenant: {restored_tenant.slug}")
    print(f"Restoring backup from: {backup_record.backup_path}")

    # Restore the backup
    service = PgDumpBackupService()
    success, message = service.restore_backup(
        backup_path=backup_record.backup_path,
        target_schema=restored_tenant.schema_name,
        drop_existing=True
    )

    if success:
        print(f"✓ Restore successful: {message}")
        print(f"Tenant data restored to schema: {restored_tenant.schema_name}")
    else:
        print(f"✗ Restore failed: {message}")
        # Clean up failed tenant
        restored_tenant.delete(force_drop=True)


def example_list_pending_deletions():
    """
    Example 7: List all tenants pending deletion

    Useful for monitoring and reporting.
    """
    print("\n=== Example 7: List Pending Deletions ===")

    from django.utils import timezone

    pending = DeletedTenantBackup.objects.filter(
        hard_deleted_at__isnull=True,
        scheduled_deletion_at__isnull=False
    ).order_by('scheduled_deletion_at')

    print(f"Found {pending.count()} tenant(s) pending deletion:\n")

    for backup in pending:
        days_until = (backup.scheduled_deletion_at - timezone.now()).days

        print(f"  {backup.tenant_slug}")
        print(f"    Soft deleted: {backup.soft_deleted_at}")
        print(f"    Scheduled: {backup.scheduled_deletion_at} ({days_until} days)")
        print(f"    Deleted by: {backup.deleted_by_email}")
        print(f"    Reason: {backup.deletion_reason}")
        print(f"    Backup: {backup.backup_path}")
        print(f"    Verified: {backup.backup_verified}")
        print()


def example_audit_trail():
    """
    Example 8: Generate audit report of all deletions

    Demonstrates how to query deletion history for compliance.
    """
    print("\n=== Example 8: Deletion Audit Trail ===")

    from datetime import timedelta
    from django.utils import timezone

    # Get deletions from last 90 days
    since = timezone.now() - timedelta(days=90)
    deletions = DeletedTenantBackup.objects.filter(
        soft_deleted_at__gte=since
    ).order_by('-soft_deleted_at')

    print(f"Deletions in last 90 days: {deletions.count()}\n")

    for backup in deletions:
        status = "PENDING" if backup.is_pending_deletion() else "COMPLETED"

        print(f"[{status}] {backup.tenant_slug} ({backup.tenant_name})")
        print(f"  Deleted: {backup.soft_deleted_at}")
        print(f"  By: {backup.deleted_by_email or 'Unknown'}")
        print(f"  Reason: {backup.deletion_reason or 'Not specified'}")
        print(f"  Backup verified: {backup.backup_verified}")

        if backup.is_pending_deletion():
            days_until = (backup.scheduled_deletion_at - timezone.now()).days
            print(f"  Hard delete in: {days_until} days")
        elif backup.is_hard_deleted():
            print(f"  Hard deleted: {backup.hard_deleted_at}")

        print()


if __name__ == '__main__':
    """
    Run examples (comment out the ones you don't want to run)
    """

    # WARNING: These examples will actually delete tenants!
    # Only run on development/test databases!

    print("="*60)
    print("TENANT DELETION EXAMPLES")
    print("="*60)

    # Uncomment the examples you want to run:

    # example_hard_delete_with_backup()
    # example_soft_delete_with_retention()
    # example_custom_backup_location()
    # example_json_backup_legacy()
    # example_verify_backup()
    # example_restore_backup()
    example_list_pending_deletions()
    example_audit_trail()

    print("\n" + "="*60)
    print("EXAMPLES COMPLETE")
    print("="*60)

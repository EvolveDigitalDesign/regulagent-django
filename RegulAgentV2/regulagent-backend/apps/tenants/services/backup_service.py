"""
Tenant backup service for safe tenant deletion.

This service creates comprehensive backups of tenant data before deletion,
including all tenant-scoped models and file artifacts.
"""
import json
import logging
import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from django.conf import settings
from django.core.serializers import serialize
from django.db import connection
from django_tenants.utils import schema_context

logger = logging.getLogger(__name__)


class TenantBackupService:
    """
    Service for creating and verifying tenant data backups.

    Backup includes:
    - Tenant metadata (name, slug, schema, owner)
    - All tenant-scoped data from overlay tables
    - Public schema references (PlanSnapshot.tenant_id, etc.)
    - List of tenant artifact files
    - Manifest with backup metadata
    """

    def __init__(self):
        self.backup_root = getattr(settings, 'TENANT_BACKUP_ROOT',
                                    os.path.join(settings.BASE_DIR, 'backups'))
        Path(self.backup_root).mkdir(parents=True, exist_ok=True)

    def backup_tenant(self, tenant, backup_dir: Optional[str] = None) -> str:
        """
        Create a complete backup of tenant data.

        Args:
            tenant: Tenant instance to backup
            backup_dir: Optional custom backup directory

        Returns:
            Path to the created backup archive

        Raises:
            Exception: If backup fails
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f"tenant_{tenant.slug}_{timestamp}"

        if backup_dir:
            backup_path = Path(backup_dir)
        else:
            backup_path = Path(self.backup_root)

        backup_path.mkdir(parents=True, exist_ok=True)
        archive_path = backup_path / f"{backup_name}.zip"

        logger.info(f"Creating backup for tenant {tenant.slug} at {archive_path}")

        try:
            with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # 1. Backup tenant metadata
                tenant_metadata = self._export_tenant_metadata(tenant)
                zipf.writestr('tenant_metadata.json',
                             json.dumps(tenant_metadata, indent=2, default=str))

                # 2. Backup tenant-scoped data
                tenant_data = self._export_tenant_data(tenant)
                zipf.writestr('tenant_data.json',
                             json.dumps(tenant_data, indent=2, default=str))

                # 3. Backup public schema references
                public_refs = self._export_public_references(tenant)
                zipf.writestr('public_references.json',
                             json.dumps(public_refs, indent=2, default=str))

                # 4. List tenant artifact files
                artifact_files = self._list_tenant_files(tenant)
                zipf.writestr('artifact_files.json',
                             json.dumps(artifact_files, indent=2, default=str))

                # 5. Create manifest
                manifest = self._create_manifest(
                    tenant,
                    timestamp,
                    tenant_data,
                    public_refs,
                    artifact_files
                )
                zipf.writestr('manifest.json',
                             json.dumps(manifest, indent=2, default=str))

            logger.info(f"Backup created successfully: {archive_path}")
            return str(archive_path)

        except Exception as e:
            logger.error(f"Failed to create backup for tenant {tenant.slug}: {e}")
            # Clean up partial backup
            if archive_path.exists():
                archive_path.unlink()
            raise

    def verify_backup(self, backup_path: str) -> Tuple[bool, str]:
        """
        Verify backup integrity.

        Args:
            backup_path: Path to backup archive

        Returns:
            Tuple of (is_valid, message)
        """
        try:
            if not os.path.exists(backup_path):
                return False, f"Backup file not found: {backup_path}"

            with zipfile.ZipFile(backup_path, 'r') as zipf:
                # Check required files
                required_files = [
                    'manifest.json',
                    'tenant_metadata.json',
                    'tenant_data.json',
                    'public_references.json',
                    'artifact_files.json'
                ]

                for required_file in required_files:
                    if required_file not in zipf.namelist():
                        return False, f"Missing required file: {required_file}"

                # Validate manifest
                manifest_data = zipf.read('manifest.json')
                manifest = json.loads(manifest_data)

                if 'tenant_slug' not in manifest:
                    return False, "Invalid manifest: missing tenant_slug"

                if 'backup_timestamp' not in manifest:
                    return False, "Invalid manifest: missing backup_timestamp"

                # Validate JSON files can be parsed
                for filename in ['tenant_metadata.json', 'tenant_data.json',
                               'public_references.json', 'artifact_files.json']:
                    try:
                        json.loads(zipf.read(filename))
                    except json.JSONDecodeError as e:
                        return False, f"Invalid JSON in {filename}: {e}"

            return True, "Backup verified successfully"

        except Exception as e:
            return False, f"Backup verification failed: {e}"

    def _export_tenant_metadata(self, tenant) -> Dict:
        """Export basic tenant metadata."""
        return {
            'id': str(tenant.id),
            'name': tenant.name,
            'slug': tenant.slug,
            'schema_name': tenant.schema_name,
            'owner_id': str(tenant.owner_id) if tenant.owner_id else None,
            'owner_email': tenant.owner.email if tenant.owner else None,
            'created_on': tenant.created_on.isoformat() if tenant.created_on else None,
            'auto_create_schema': tenant.auto_create_schema,
            'auto_drop_schema': tenant.auto_drop_schema,
        }

    def _export_tenant_data(self, tenant) -> Dict:
        """
        Export all tenant-scoped data from overlay tables.

        Uses schema_context to query tenant schema.
        """
        # Import models here to avoid circular imports
        from apps.tenant_overlay.models import (
            WellEngagement,
            CanonicalFacts,
            TenantGuardrailPolicy,
            PlanModification,
            TenantArtifact,
        )

        data = {}

        with schema_context(tenant.schema_name):
            # Export WellEngagement
            engagements = WellEngagement.objects.filter(tenant_id=tenant.id)
            data['well_engagements'] = json.loads(
                serialize('json', engagements)
            )

            # Export CanonicalFacts
            facts = CanonicalFacts.objects.all()
            data['canonical_facts'] = json.loads(
                serialize('json', facts)
            )

            # Export TenantGuardrailPolicy
            policies = TenantGuardrailPolicy.objects.filter(tenant_id=tenant.id)
            data['guardrail_policies'] = json.loads(
                serialize('json', policies)
            )

            # Export PlanModification (tenant field may be null, filter by tenant_id)
            modifications = PlanModification.objects.filter(
                tenant_id=tenant.id
            )
            data['plan_modifications'] = json.loads(
                serialize('json', modifications)
            )

            # Export TenantArtifact
            artifacts = TenantArtifact.objects.filter(tenant_id=tenant.id)
            data['tenant_artifacts'] = json.loads(
                serialize('json', artifacts)
            )

        # Add counts for verification
        data['_counts'] = {
            'well_engagements': len(data['well_engagements']),
            'canonical_facts': len(data['canonical_facts']),
            'guardrail_policies': len(data['guardrail_policies']),
            'plan_modifications': len(data['plan_modifications']),
            'tenant_artifacts': len(data['tenant_artifacts']),
        }

        return data

    def _export_public_references(self, tenant) -> Dict:
        """
        Export public schema records that reference this tenant.

        This includes PlanSnapshot.tenant_id references.
        """
        from apps.public_core.models import PlanSnapshot

        data = {}

        # Export PlanSnapshots with this tenant_id
        snapshots = PlanSnapshot.objects.filter(tenant_id=tenant.id)
        data['plan_snapshots'] = json.loads(
            serialize('json', snapshots)
        )

        data['_counts'] = {
            'plan_snapshots': len(data['plan_snapshots']),
        }

        return data

    def _list_tenant_files(self, tenant) -> List[Dict]:
        """
        List all files associated with tenant artifacts.

        Returns list of file metadata (path, size, etc.)
        Does NOT copy file contents to backup (too large).
        """
        from apps.tenant_overlay.models import TenantArtifact

        files = []

        with schema_context(tenant.schema_name):
            artifacts = TenantArtifact.objects.filter(tenant_id=tenant.id)

            for artifact in artifacts:
                file_info = {
                    'artifact_id': str(artifact.id),
                    'artifact_type': artifact.artifact_type,
                    'file_path': artifact.file_path,
                    'content_type': artifact.content_type,
                    'size_bytes': artifact.size_bytes,
                    'sha256': artifact.sha256,
                    'created_at': artifact.created_at.isoformat(),
                }

                # Check if file exists
                if settings.USE_S3:
                    file_info['storage'] = 's3'
                    file_info['exists'] = 'unknown'  # Would need S3 client to check
                else:
                    file_info['storage'] = 'local'
                    file_path = os.path.join(settings.MEDIA_ROOT, artifact.file_path)
                    file_info['exists'] = os.path.exists(file_path)
                    if file_info['exists']:
                        file_info['actual_size'] = os.path.getsize(file_path)

                files.append(file_info)

        return files

    def list_tenant_files(self, tenant) -> List[Dict]:
        """
        Public interface for listing tenant files.

        Args:
            tenant: Tenant instance

        Returns:
            List of file metadata dicts
        """
        return self._list_tenant_files(tenant)

    def _create_manifest(
        self,
        tenant,
        timestamp: str,
        tenant_data: Dict,
        public_refs: Dict,
        artifact_files: List[Dict]
    ) -> Dict:
        """Create backup manifest with metadata."""
        return {
            'backup_version': '1.0',
            'backup_timestamp': timestamp,
            'tenant_id': str(tenant.id),
            'tenant_slug': tenant.slug,
            'tenant_name': tenant.name,
            'schema_name': tenant.schema_name,
            'django_version': getattr(settings, 'DJANGO_VERSION', 'unknown'),
            'database_engine': connection.settings_dict.get('ENGINE', 'unknown'),
            'counts': {
                'tenant_data': tenant_data.get('_counts', {}),
                'public_references': public_refs.get('_counts', {}),
                'artifact_files': len(artifact_files),
            },
            'storage_backend': 's3' if settings.USE_S3 else 'local',
            'notes': f"Backup created before tenant deletion",
        }

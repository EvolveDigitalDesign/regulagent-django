"""
PostgreSQL pg_dump-based backup service for tenant schemas.

This service creates schema-level backups using pg_dump for safe tenant deletion.
"""
import hashlib
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)


class PgDumpBackupService:
    """
    Service for creating PostgreSQL schema backups using pg_dump.

    This provides a database-level backup that can be restored using pg_restore,
    making it suitable for disaster recovery and compliance requirements.
    """

    def __init__(self):
        self.backup_root = getattr(
            settings,
            'TENANT_BACKUP_ROOT',
            '/tmp/tenant_backups/'
        )
        Path(self.backup_root).mkdir(parents=True, exist_ok=True)

    def backup_tenant_schema(
        self,
        tenant,
        backup_dir: Optional[str] = None
    ) -> Tuple[str, int, str]:
        """
        Create a pg_dump backup of the tenant schema.

        Args:
            tenant: Tenant instance to backup
            backup_dir: Optional custom backup directory

        Returns:
            Tuple of (backup_path, size_bytes, checksum_sha256)

        Raises:
            subprocess.CalledProcessError: If pg_dump fails
            Exception: If backup verification fails
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f"tenant_{tenant.slug}_{timestamp}.sql"

        if backup_dir:
            backup_path = Path(backup_dir)
        else:
            backup_path = Path(self.backup_root)

        backup_path.mkdir(parents=True, exist_ok=True)
        full_backup_path = backup_path / backup_name

        logger.info(
            f"Creating pg_dump backup for tenant {tenant.slug} "
            f"(schema: {tenant.schema_name}) at {full_backup_path}"
        )

        # Get database connection parameters
        db_config = connection.settings_dict

        # Build pg_dump command
        # Using custom format (-Fc) for compression and efficient restore
        cmd = [
            'pg_dump',
            '-Fc',  # Custom format (compressed)
            '-n', tenant.schema_name,  # Only dump this schema
            '-f', str(full_backup_path),
            '-h', db_config.get('HOST', 'localhost'),
            '-p', str(db_config.get('PORT', 5432)),
            '-U', db_config.get('USER', 'postgres'),
            '-d', db_config.get('NAME', 'regulagent'),
        ]

        # Set password via environment variable
        env = os.environ.copy()
        if db_config.get('PASSWORD'):
            env['PGPASSWORD'] = db_config['PASSWORD']

        try:
            # Execute pg_dump
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                check=True,
                timeout=300  # 5 minute timeout
            )

            if result.stderr:
                logger.warning(f"pg_dump stderr: {result.stderr}")

            # Verify backup file was created
            if not full_backup_path.exists():
                raise Exception(f"Backup file was not created: {full_backup_path}")

            # Get file size
            size_bytes = full_backup_path.stat().st_size
            logger.info(f"Backup created: {size_bytes} bytes")

            # Calculate checksum
            checksum = self._calculate_checksum(str(full_backup_path))
            logger.info(f"Backup checksum (SHA256): {checksum}")

            # Verify backup can be read
            verify_result = self._verify_backup(str(full_backup_path), db_config)
            if not verify_result[0]:
                raise Exception(f"Backup verification failed: {verify_result[1]}")

            logger.info(f"Backup created and verified successfully: {full_backup_path}")
            return str(full_backup_path), size_bytes, checksum

        except subprocess.TimeoutExpired as e:
            logger.error(f"pg_dump timeout for tenant {tenant.slug}: {e}")
            # Clean up partial backup
            if full_backup_path.exists():
                full_backup_path.unlink()
            raise Exception(f"Backup timeout after 5 minutes")

        except subprocess.CalledProcessError as e:
            logger.error(
                f"pg_dump failed for tenant {tenant.slug}: "
                f"returncode={e.returncode}, stderr={e.stderr}"
            )
            # Clean up partial backup
            if full_backup_path.exists():
                full_backup_path.unlink()
            raise Exception(f"pg_dump failed: {e.stderr}")

        except Exception as e:
            logger.error(f"Backup failed for tenant {tenant.slug}: {e}")
            # Clean up partial backup
            if full_backup_path.exists():
                full_backup_path.unlink()
            raise

    def _calculate_checksum(self, file_path: str) -> str:
        """
        Calculate SHA256 checksum of a file.

        Args:
            file_path: Path to file

        Returns:
            Hex digest of SHA256 hash
        """
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            # Read file in chunks to handle large files
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _verify_backup(
        self,
        backup_path: str,
        db_config: dict
    ) -> Tuple[bool, str]:
        """
        Verify backup integrity using pg_restore --list.

        Args:
            backup_path: Path to backup file
            db_config: Database configuration dict

        Returns:
            Tuple of (is_valid, message)
        """
        try:
            # Use pg_restore --list to verify backup is readable
            cmd = [
                'pg_restore',
                '--list',
                backup_path,
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=30
            )

            # Check if we got any output (list of objects in backup)
            if not result.stdout:
                return False, "pg_restore --list produced no output"

            # Count number of items in backup
            line_count = len(result.stdout.strip().split('\n'))
            logger.info(f"Backup contains {line_count} objects")

            return True, f"Backup verified successfully ({line_count} objects)"

        except subprocess.CalledProcessError as e:
            return False, f"pg_restore verification failed: {e.stderr}"
        except Exception as e:
            return False, f"Verification error: {str(e)}"

    def verify_backup(self, backup_path: str) -> Tuple[bool, str]:
        """
        Public interface to verify backup integrity.

        Args:
            backup_path: Path to backup file

        Returns:
            Tuple of (is_valid, message)
        """
        if not os.path.exists(backup_path):
            return False, f"Backup file not found: {backup_path}"

        db_config = connection.settings_dict
        return self._verify_backup(backup_path, db_config)

    def restore_backup(
        self,
        backup_path: str,
        target_schema: str,
        drop_existing: bool = False
    ) -> Tuple[bool, str]:
        """
        Restore a backup to a target schema.

        Args:
            backup_path: Path to backup file
            target_schema: Name of schema to restore to
            drop_existing: Whether to drop existing schema first

        Returns:
            Tuple of (success, message)

        Warning:
            This will overwrite data in the target schema!
        """
        if not os.path.exists(backup_path):
            return False, f"Backup file not found: {backup_path}"

        db_config = connection.settings_dict

        try:
            # Build pg_restore command
            cmd = [
                'pg_restore',
                '-h', db_config.get('HOST', 'localhost'),
                '-p', str(db_config.get('PORT', 5432)),
                '-U', db_config.get('USER', 'postgres'),
                '-d', db_config.get('NAME', 'regulagent'),
                '-n', target_schema,
            ]

            if drop_existing:
                cmd.append('--clean')

            cmd.append(backup_path)

            # Set password via environment variable
            env = os.environ.copy()
            if db_config.get('PASSWORD'):
                env['PGPASSWORD'] = db_config['PASSWORD']

            # Execute pg_restore
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                check=True,
                timeout=600  # 10 minute timeout for restore
            )

            if result.stderr:
                logger.warning(f"pg_restore stderr: {result.stderr}")

            logger.info(f"Backup restored successfully to schema: {target_schema}")
            return True, f"Restore completed successfully to {target_schema}"

        except subprocess.TimeoutExpired:
            return False, "Restore timeout after 10 minutes"
        except subprocess.CalledProcessError as e:
            return False, f"pg_restore failed: {e.stderr}"
        except Exception as e:
            return False, f"Restore error: {str(e)}"

"""
Cold Storage Manager for Neubus documents.

Manages the local filesystem storage for downloaded Neubus PDFs.
Append-only design: files are never modified or deleted once stored.

Storage layout:
    MEDIA_ROOT/rrc/neubus/{lease_id}/
        manifest.json       — metadata about all stored files
        689_17-4328543.pdf  — original Neubus filenames preserved
        688_17-4328543.pdf
        ...
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)


class ColdStorageManager:
    """
    Manages cold storage for Neubus documents for a single lease.

    Append-only: never modifies or deletes existing files.
    Uses SHA-256 hashes for deduplication.
    """

    def __init__(self, lease_id: str):
        self.lease_id = lease_id
        self.base_dir = Path(settings.MEDIA_ROOT) / "rrc" / "neubus" / lease_id
        self._manifest: Optional[Dict[str, Any]] = None

    @property
    def manifest_path(self) -> Path:
        return self.base_dir / "manifest.json"

    @property
    def manifest(self) -> Dict[str, Any]:
        """Load manifest from disk, creating if needed."""
        if self._manifest is None:
            self._manifest = self._load_manifest()
        return self._manifest

    def _load_manifest(self) -> Dict[str, Any]:
        """Read manifest.json from disk."""
        if self.manifest_path.exists():
            try:
                with open(self.manifest_path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to read manifest for lease {self.lease_id}: {e}")

        return {
            "lease_id": self.lease_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": {},
        }

    def _save_manifest(self) -> None:
        """Write manifest.json to disk."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.manifest["updated_at"] = datetime.now(timezone.utc).isoformat()

        with open(self.manifest_path, "w") as f:
            json.dump(self.manifest, f, indent=2, default=str)

    def file_path(self, filename: str) -> Path:
        """Get the full path for a file in cold storage."""
        return self.base_dir / filename

    def file_exists(self, filename: str) -> bool:
        """Check if a file exists in cold storage."""
        return self.file_path(filename).exists()

    def is_known(self, filename: str) -> bool:
        """Check if a file is recorded in the manifest (even if not on disk)."""
        return filename in self.manifest.get("files", {})

    def get_file_hash(self, filename: str) -> Optional[str]:
        """Get the stored hash for a file, or None if not known."""
        return self.manifest.get("files", {}).get(filename, {}).get("hash")

    def is_stale(self, hours: int = 24) -> bool:
        """Check if the manifest is older than the given threshold."""
        updated = self.manifest.get("updated_at")
        if not updated:
            return True

        try:
            last_update = datetime.fromisoformat(updated)
            if last_update.tzinfo is None:
                last_update = last_update.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - last_update
            return age.total_seconds() > (hours * 3600)
        except (ValueError, TypeError):
            return True

    def register_file(
        self,
        filename: str,
        size_bytes: int,
        file_hash: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Register a downloaded file in the manifest.

        Args:
            filename: The filename (must already exist on disk)
            size_bytes: File size in bytes
            file_hash: SHA-256 hash (computed if not provided)
            metadata: Additional metadata to store
        """
        if not file_hash:
            file_hash = self.compute_hash(self.file_path(filename))

        entry = {
            "hash": file_hash,
            "size_bytes": size_bytes,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            entry["metadata"] = metadata

        self.manifest.setdefault("files", {})[filename] = entry
        self._save_manifest()

    def should_download(self, filename: str, remote_hash: Optional[str] = None) -> bool:
        """
        Determine if a file needs to be downloaded.

        Returns False if:
        - File exists on disk AND is in manifest
        - If remote_hash provided, must match stored hash
        """
        if not self.file_exists(filename):
            return True

        if not self.is_known(filename):
            return True

        if remote_hash:
            stored_hash = self.get_file_hash(filename)
            if stored_hash and stored_hash != remote_hash:
                logger.warning(
                    f"Hash mismatch for {filename}: stored={stored_hash[:12]}... "
                    f"remote={remote_hash[:12]}..."
                )
                return True

        return False

    def list_files(self) -> List[str]:
        """List all filenames in the manifest."""
        return list(self.manifest.get("files", {}).keys())

    def get_all_metadata(self) -> Dict[str, Any]:
        """Get the full manifest as a dict."""
        return dict(self.manifest)

    @staticmethod
    def compute_hash(file_path: Path) -> str:
        """Compute SHA-256 hash of a file."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

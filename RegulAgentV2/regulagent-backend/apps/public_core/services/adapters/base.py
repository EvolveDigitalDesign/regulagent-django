from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class DocumentSpec:
    """Unified document descriptor across all states."""
    filename: str
    url: Optional[str] = None         # remote URL (NM)
    local_path: Optional[str] = None  # local file path (TX after download)
    file_size: Optional[str] = None
    date: Optional[str] = None
    doc_type: Optional[str] = None    # c_101, c_103, w2, w15, etc.
    metadata: Optional[Dict[str, Any]] = None


class StateAdapter(ABC):
    _last_fetch_error: Optional[Dict[str, Any]] = None

    @abstractmethod
    def state_code(self) -> str:
        """Return 2-letter state code (TX, NM, UT)."""
        ...

    @abstractmethod
    def fetch_document_list(self, api_number: str) -> List[DocumentSpec]:
        """Fetch list of available documents for the well."""
        ...

    @abstractmethod
    def download_document(self, doc: DocumentSpec) -> Path:
        """Download a document and return local file path."""
        ...

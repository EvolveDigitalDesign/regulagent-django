from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import List

from apps.public_core.services.nm_document_fetcher import NMDocumentFetcher
from apps.public_core.services.adapters.base import DocumentSpec, StateAdapter

logger = logging.getLogger(__name__)


class NMAdapter(StateAdapter):
    def state_code(self) -> str:
        return "NM"

    def fetch_document_list(self, api_number: str) -> List[DocumentSpec]:
        with NMDocumentFetcher() as fetcher:
            nm_docs = fetcher.list_documents(api_number)

        return [
            DocumentSpec(
                filename=doc.filename,
                url=doc.url,
                local_path=None,
                file_size=doc.file_size,
                date=doc.date,
                doc_type=doc.doc_type,
            )
            for doc in nm_docs
        ]

    def download_document(self, doc: DocumentSpec) -> Path:
        with NMDocumentFetcher() as fetcher:
            from apps.public_core.services.nm_document_fetcher import NMDocument
            nm_doc = NMDocument(
                filename=doc.filename,
                url=doc.url,
                file_size=doc.file_size,
                date=doc.date,
                doc_type=doc.doc_type,
            )
            pdf_bytes = fetcher.download_document(nm_doc)

        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(pdf_bytes)
        tmp.flush()
        tmp.close()
        local_path = Path(tmp.name)
        logger.info(f"NMAdapter: saved {doc.filename} to {local_path}")
        return local_path

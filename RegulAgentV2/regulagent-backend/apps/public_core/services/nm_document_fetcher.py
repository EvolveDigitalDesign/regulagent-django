"""
NM OCD Document Fetcher

Downloads well file documents from NM OCD imaging portal.
"""
from __future__ import annotations

import re
import logging
from typing import List, Optional
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class NMDocument:
    """Metadata for an NM well document."""
    filename: str
    url: str
    file_size: Optional[str] = None
    date: Optional[str] = None
    doc_type: Optional[str] = None  # C-101, C-103, etc. if detectable


class NMDocumentFetcher:
    """Fetcher for NM OCD well documents."""

    BASE_URL = "https://ocdimage.emnrd.nm.gov/imaging/WellFileView.aspx"

    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout
        self.session = requests.Session()

    def _api_to_api14(self, api: str) -> str:
        """
        Convert any API format to 14-digit no-dash format.

        Args:
            api: API number in any format (with or without dashes)

        Returns:
            14-digit API number with no dashes

        Raises:
            ValueError: If API cannot be converted to valid 14-digit format
        """
        digits = re.sub(r'[^0-9]', '', api)
        if len(digits) == 10:
            digits = digits + "0000"
        if len(digits) != 14:
            raise ValueError(f"Invalid API number: {api}")
        return digits

    def list_documents(self, api: str) -> List[NMDocument]:
        """
        List all available documents for a well.

        Args:
            api: API number in any format

        Returns:
            List of NMDocument with metadata
        """
        api14 = self._api_to_api14(api)
        url = f"{self.BASE_URL}?RefType=WF&RefID={api14}"

        logger.info(f"Listing NM documents: {url}")
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()

        return self._parse_document_list(response.text)

    def _parse_document_list(self, html: str) -> List[NMDocument]:
        """Parse document list from HTML."""
        soup = BeautifulSoup(html, 'html.parser')
        documents = []

        # Find all PDF links
        for link in soup.find_all('a', href=re.compile(r'\.pdf$', re.IGNORECASE)):
            href = link.get('href', '')
            if href:
                # Make absolute URL if relative
                if not href.startswith('http'):
                    href = f"https://ocdimage.emnrd.nm.gov{href}"

                filename = href.split('/')[-1]

                # Try to detect document type from filename
                doc_type = self._detect_doc_type(filename)

                documents.append(NMDocument(
                    filename=filename,
                    url=href,
                    file_size=None,  # Could parse from page if available
                    date=None,  # Could parse from page if available
                    doc_type=doc_type
                ))

        return documents

    def _detect_doc_type(self, filename: str) -> Optional[str]:
        """Try to detect document type from filename."""
        filename_lower = filename.lower()
        if 'c-101' in filename_lower or 'c101' in filename_lower:
            return 'C-101'
        elif 'c-103' in filename_lower or 'c103' in filename_lower:
            return 'C-103'
        elif 'c-105' in filename_lower or 'c105' in filename_lower:
            return 'C-105'
        return None

    def download_document(self, doc: NMDocument) -> bytes:
        """
        Download a single document.

        Args:
            doc: NMDocument with URL

        Returns:
            PDF bytes
        """
        logger.info(f"Downloading: {doc.filename}")
        response = self.session.get(doc.url, timeout=self.timeout)
        response.raise_for_status()
        return response.content

    def download_all_documents(self, api: str) -> List[tuple[NMDocument, bytes]]:
        """
        Download all documents for a well.

        Args:
            api: API number

        Returns:
            List of (NMDocument, bytes) tuples
        """
        documents = self.list_documents(api)
        results = []

        for doc in documents:
            try:
                content = self.download_document(doc)
                results.append((doc, content))
            except Exception as e:
                logger.error(f"Failed to download {doc.filename}: {e}")

        return results

    def get_combined_pdf_url(self, api: str) -> str:
        """
        Get the URL for the combined PDF download.

        Note: The actual "View All" functionality may require
        form submission or JavaScript. This returns the base URL.
        """
        api14 = self._api_to_api14(api)
        return f"{self.BASE_URL}?RefType=WF&RefID={api14}&ViewAll=true"

    def close(self):
        """Close HTTP session."""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# Convenience functions
def list_nm_documents(api: str) -> List[NMDocument]:
    """List documents for an NM well."""
    with NMDocumentFetcher() as fetcher:
        return fetcher.list_documents(api)


def download_nm_document(url: str) -> bytes:
    """Download a single document by URL."""
    response = requests.get(url, timeout=60.0)
    response.raise_for_status()
    return response.content

"""
Integration tests for NM OCD Document Fetcher

These tests make actual HTTP calls to the NM OCD imaging portal.
They are marked with @pytest.mark.integration and are skipped by default.

Run with: pytest -m integration
"""

import pytest
from apps.public_core.services.nm_document_fetcher import (
    NMDocumentFetcher,
    list_nm_documents,
)


@pytest.mark.integration
@pytest.mark.skip(reason="Integration test - requires network access")
class TestNMDocumentFetcherIntegration:
    """Integration tests against real NM OCD portal."""

    def test_list_documents_real_well(self):
        """Test listing documents for a real well (30-015-28692)."""
        with NMDocumentFetcher() as fetcher:
            documents = fetcher.list_documents("30-015-28692")

            # We expect to find some documents
            assert len(documents) > 0

            # Check that documents have required fields
            for doc in documents:
                assert doc.filename
                assert doc.url
                assert doc.url.startswith("https://")
                # Some documents may not have a detected type
                print(f"Found: {doc.filename} - Type: {doc.doc_type}")

    def test_get_combined_pdf_url_format(self):
        """Test that combined PDF URL is properly formatted."""
        with NMDocumentFetcher() as fetcher:
            url = fetcher.get_combined_pdf_url("30-015-28692")

            assert "https://ocdimage.emnrd.nm.gov/imaging/WellFileView.aspx" in url
            assert "RefType=WF" in url
            assert "RefID=30015286920000" in url
            assert "ViewAll=true" in url

    def test_download_single_document(self):
        """Test downloading a single document."""
        # First list documents to get a URL
        with NMDocumentFetcher() as fetcher:
            documents = fetcher.list_documents("30-015-28692")

            if documents:
                # Try to download the first document
                content = fetcher.download_document(documents[0])

                # Verify we got PDF content
                assert len(content) > 0
                # PDF files start with %PDF
                assert content[:4] == b'%PDF'

                print(f"Downloaded {documents[0].filename}: {len(content)} bytes")

    def test_convenience_function(self):
        """Test convenience function for listing documents."""
        documents = list_nm_documents("30-015-28692")

        assert len(documents) > 0
        print(f"Found {len(documents)} documents using convenience function")


@pytest.mark.integration
@pytest.mark.skip(reason="Manual test - use to explore well files")
def test_explore_well_files():
    """
    Manual test to explore what documents are available for a well.
    Useful for understanding the portal structure.

    Run with: pytest -m integration -k explore --no-skip
    """
    with NMDocumentFetcher() as fetcher:
        # Test with the example well
        api = "30-015-28692"
        print(f"\nExploring documents for API: {api}")
        print(f"Well file URL: {fetcher.BASE_URL}?RefType=WF&RefID={fetcher._api_to_api14(api)}")

        documents = fetcher.list_documents(api)

        print(f"\nFound {len(documents)} documents:")
        for i, doc in enumerate(documents, 1):
            print(f"\n{i}. {doc.filename}")
            print(f"   URL: {doc.url}")
            print(f"   Type: {doc.doc_type or 'Unknown'}")

        # Try the combined PDF URL
        combined_url = fetcher.get_combined_pdf_url(api)
        print(f"\nCombined PDF URL: {combined_url}")

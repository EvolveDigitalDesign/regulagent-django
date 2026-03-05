"""
Tests for NM Well API Endpoints

Tests the REST API endpoints for NM OCD well data lookup and document access.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from apps.public_core.services.nm_well_scraper import NMWellData
from apps.public_core.services.nm_document_fetcher import NMDocument


@pytest.fixture
def test_tenant(db):
    """Create a test tenant."""
    from apps.tenants.models import Tenant
    tenant = Tenant.objects.create(
        schema_name='test_tenant',
        name='Test Tenant',
        paid_until='2025-12-31',
        on_trial=False
    )
    return tenant


@pytest.fixture
def mock_user(db, test_tenant):
    """Create a mock authenticated user."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(
        email='test@example.com',
        password='testpass123'
    )
    user.tenants.add(test_tenant)
    return user


@pytest.fixture
def api_client():
    """Create an API client."""
    return APIClient()


@pytest.fixture
def authenticated_client(api_client, mock_user):
    """Create an authenticated API client."""
    api_client.force_authenticate(user=mock_user)
    return api_client


@pytest.fixture
def sample_well_data():
    """Sample NM well data for testing."""
    return NMWellData(
        api10="30-015-28692",
        api14="30015286920000",
        well_name="FEDERAL 24-19 1H",
        operator_name="EOG RESOURCES INC",
        operator_number="7377",
        status="PRODUCING",
        well_type="OIL",
        direction="HORIZONTAL",
        surface_location="24-19S-32E",
        latitude=32.7574387,
        longitude=-104.0298615,
        elevation_ft=3450.0,
        proposed_depth_ft=21000,
        tvd_ft=10500,
        formation="WOLFCAMP",
        spud_date="01/15/2019",
        completion_date="03/20/2019",
    )


@pytest.fixture
def sample_documents():
    """Sample NM documents for testing."""
    return [
        NMDocument(
            filename="C-101_permit.pdf",
            url="https://ocdimage.emnrd.nm.gov/path/C-101_permit.pdf",
            doc_type="C-101"
        ),
        NMDocument(
            filename="C-103_sundry.pdf",
            url="https://ocdimage.emnrd.nm.gov/path/C-103_sundry.pdf",
            doc_type="C-103"
        ),
        NMDocument(
            filename="C-105_completion.pdf",
            url="https://ocdimage.emnrd.nm.gov/path/C-105_completion.pdf",
            doc_type="C-105"
        ),
    ]


@pytest.mark.django_db
class TestNMWellDetailView:
    """Test the NM well detail endpoint."""

    def test_unauthenticated_request_fails(self, api_client):
        """Test that unauthenticated requests are rejected."""
        url = reverse('nm_well_detail', kwargs={'api': '30-015-28692'})
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @patch('apps.public_core.views.nm_wells.NMWellScraper')
    def test_fetch_well_success(self, mock_scraper_class, authenticated_client, sample_well_data):
        """Test successful well data fetch."""
        # Setup mock
        mock_scraper = MagicMock()
        mock_scraper.__enter__ = Mock(return_value=mock_scraper)
        mock_scraper.__exit__ = Mock(return_value=False)
        mock_scraper.fetch_well = Mock(return_value=sample_well_data)
        mock_scraper_class.return_value = mock_scraper

        # Make request
        url = reverse('nm_well_detail', kwargs={'api': '30-015-28692'})
        response = authenticated_client.get(url)

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['api10'] == "30-015-28692"
        assert data['api14'] == "30015286920000"
        assert data['well_name'] == "FEDERAL 24-19 1H"
        assert data['operator_name'] == "EOG RESOURCES INC"
        assert data['operator_number'] == "7377"
        assert data['status'] == "PRODUCING"
        assert data['well_type'] == "OIL"
        assert data['direction'] == "HORIZONTAL"
        assert data['latitude'] == pytest.approx(32.7574387, rel=1e-6)
        assert data['longitude'] == pytest.approx(-104.0298615, rel=1e-6)

        # Verify scraper was called correctly
        mock_scraper.fetch_well.assert_called_once_with('30-015-28692', include_raw_html=False)

    @patch('apps.public_core.views.nm_wells.NMWellScraper')
    def test_fetch_well_with_raw_html(self, mock_scraper_class, authenticated_client, sample_well_data):
        """Test fetching well data with raw HTML included."""
        # Add raw HTML to sample data
        sample_well_data.raw_html = "<html><body>Test HTML</body></html>"

        # Setup mock
        mock_scraper = MagicMock()
        mock_scraper.__enter__ = Mock(return_value=mock_scraper)
        mock_scraper.__exit__ = Mock(return_value=False)
        mock_scraper.fetch_well = Mock(return_value=sample_well_data)
        mock_scraper_class.return_value = mock_scraper

        # Make request with include_raw_html=true
        url = reverse('nm_well_detail', kwargs={'api': '30-015-28692'})
        response = authenticated_client.get(url, {'include_raw_html': 'true'})

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert 'raw_html' in data
        assert data['raw_html'] == "<html><body>Test HTML</body></html>"

        # Verify scraper was called with include_raw_html=True
        mock_scraper.fetch_well.assert_called_once_with('30-015-28692', include_raw_html=True)

    @patch('apps.public_core.views.nm_wells.NMWellScraper')
    def test_fetch_well_invalid_api_format(self, mock_scraper_class, authenticated_client):
        """Test handling of invalid API format."""
        # Setup mock to raise ValueError
        mock_scraper = MagicMock()
        mock_scraper.__enter__ = Mock(return_value=mock_scraper)
        mock_scraper.__exit__ = Mock(return_value=False)
        mock_scraper.fetch_well = Mock(side_effect=ValueError("Invalid NM API number"))
        mock_scraper_class.return_value = mock_scraper

        # Make request
        url = reverse('nm_well_detail', kwargs={'api': 'invalid-api'})
        response = authenticated_client.get(url)

        # Assertions
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert 'detail' in data
        assert 'Invalid NM API number' in data['detail']

    @patch('apps.public_core.views.nm_wells.NMWellScraper')
    def test_fetch_well_network_error(self, mock_scraper_class, authenticated_client):
        """Test handling of network errors."""
        # Setup mock to raise generic exception
        mock_scraper = MagicMock()
        mock_scraper.__enter__ = Mock(return_value=mock_scraper)
        mock_scraper.__exit__ = Mock(return_value=False)
        mock_scraper.fetch_well = Mock(side_effect=Exception("Connection timeout"))
        mock_scraper_class.return_value = mock_scraper

        # Make request
        url = reverse('nm_well_detail', kwargs={'api': '30-015-28692'})
        response = authenticated_client.get(url)

        # Assertions
        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert 'detail' in data
        assert 'Connection timeout' in data['detail']

    @patch('apps.public_core.views.nm_wells.NMWellScraper')
    def test_fetch_well_with_different_api_formats(self, mock_scraper_class, authenticated_client, sample_well_data):
        """Test that different API formats are accepted."""
        # Setup mock
        mock_scraper = MagicMock()
        mock_scraper.__enter__ = Mock(return_value=mock_scraper)
        mock_scraper.__exit__ = Mock(return_value=False)
        mock_scraper.fetch_well = Mock(return_value=sample_well_data)
        mock_scraper_class.return_value = mock_scraper

        # Test different API formats
        api_formats = [
            '30-015-28692',      # API-10 with dashes
            '3001528692',        # API-10 without dashes
            '30015286920000',    # API-14 without dashes
            '30-015-28692-0000', # API-14 with dashes
        ]

        for api_format in api_formats:
            url = reverse('nm_well_detail', kwargs={'api': api_format})
            response = authenticated_client.get(url)
            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data['api10'] == "30-015-28692"


@pytest.mark.django_db
class TestNMWellDocumentsView:
    """Test the NM well documents endpoint."""

    def test_unauthenticated_request_fails(self, api_client):
        """Test that unauthenticated requests are rejected."""
        url = reverse('nm_well_documents', kwargs={'api': '30-015-28692'})
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @patch('apps.public_core.views.nm_wells.NMDocumentFetcher')
    def test_list_documents_success(self, mock_fetcher_class, authenticated_client, sample_documents):
        """Test successful document listing."""
        # Setup mock
        mock_fetcher = MagicMock()
        mock_fetcher.__enter__ = Mock(return_value=mock_fetcher)
        mock_fetcher.__exit__ = Mock(return_value=False)
        mock_fetcher.list_documents = Mock(return_value=sample_documents)
        mock_fetcher_class.return_value = mock_fetcher

        # Make request
        url = reverse('nm_well_documents', kwargs={'api': '30-015-28692'})
        response = authenticated_client.get(url)

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['api'] == '30-015-28692'
        assert data['count'] == 3
        assert len(data['documents']) == 3

        # Check first document
        doc1 = data['documents'][0]
        assert doc1['filename'] == "C-101_permit.pdf"
        assert doc1['url'] == "https://ocdimage.emnrd.nm.gov/path/C-101_permit.pdf"
        assert doc1['doc_type'] == "C-101"

        # Check second document
        doc2 = data['documents'][1]
        assert doc2['filename'] == "C-103_sundry.pdf"
        assert doc2['doc_type'] == "C-103"

        # Verify fetcher was called correctly
        mock_fetcher.list_documents.assert_called_once_with('30-015-28692')

    @patch('apps.public_core.views.nm_wells.NMDocumentFetcher')
    def test_list_documents_empty(self, mock_fetcher_class, authenticated_client):
        """Test listing documents when none are available."""
        # Setup mock to return empty list
        mock_fetcher = MagicMock()
        mock_fetcher.__enter__ = Mock(return_value=mock_fetcher)
        mock_fetcher.__exit__ = Mock(return_value=False)
        mock_fetcher.list_documents = Mock(return_value=[])
        mock_fetcher_class.return_value = mock_fetcher

        # Make request
        url = reverse('nm_well_documents', kwargs={'api': '30-015-28692'})
        response = authenticated_client.get(url)

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['count'] == 0
        assert data['documents'] == []

    @patch('apps.public_core.views.nm_wells.NMDocumentFetcher')
    def test_list_documents_invalid_api(self, mock_fetcher_class, authenticated_client):
        """Test handling of invalid API format."""
        # Setup mock to raise ValueError
        mock_fetcher = MagicMock()
        mock_fetcher.__enter__ = Mock(return_value=mock_fetcher)
        mock_fetcher.__exit__ = Mock(return_value=False)
        mock_fetcher.list_documents = Mock(side_effect=ValueError("Invalid API number"))
        mock_fetcher_class.return_value = mock_fetcher

        # Make request
        url = reverse('nm_well_documents', kwargs={'api': 'invalid'})
        response = authenticated_client.get(url)

        # Assertions
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert 'detail' in data
        assert 'Invalid API number' in data['detail']

    @patch('apps.public_core.views.nm_wells.NMDocumentFetcher')
    def test_list_documents_network_error(self, mock_fetcher_class, authenticated_client):
        """Test handling of network errors."""
        # Setup mock to raise generic exception
        mock_fetcher = MagicMock()
        mock_fetcher.__enter__ = Mock(return_value=mock_fetcher)
        mock_fetcher.__exit__ = Mock(return_value=False)
        mock_fetcher.list_documents = Mock(side_effect=Exception("Network timeout"))
        mock_fetcher_class.return_value = mock_fetcher

        # Make request
        url = reverse('nm_well_documents', kwargs={'api': '30-015-28692'})
        response = authenticated_client.get(url)

        # Assertions
        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert 'detail' in data
        assert 'Network timeout' in data['detail']


@pytest.mark.django_db
class TestNMWellCombinedPDFView:
    """Test the NM well combined PDF endpoint."""

    def test_unauthenticated_request_fails(self, api_client):
        """Test that unauthenticated requests are rejected."""
        url = reverse('nm_well_combined_pdf', kwargs={'api': '30-015-28692'})
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @patch('apps.public_core.views.nm_wells.NMDocumentFetcher')
    def test_get_combined_pdf_url_success(self, mock_fetcher_class, authenticated_client):
        """Test successful combined PDF URL retrieval."""
        # Setup mock
        mock_fetcher = MagicMock()
        mock_fetcher.__enter__ = Mock(return_value=mock_fetcher)
        mock_fetcher.__exit__ = Mock(return_value=False)
        mock_fetcher.get_combined_pdf_url = Mock(
            return_value="https://ocdimage.emnrd.nm.gov/imaging/WellFileView.aspx?RefType=WF&RefID=30015286920000&ViewAll=true"
        )
        mock_fetcher._api_to_api14 = Mock(return_value="30015286920000")
        mock_fetcher_class.return_value = mock_fetcher

        # Make request
        url = reverse('nm_well_combined_pdf', kwargs={'api': '30-015-28692'})
        response = authenticated_client.get(url)

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert 'url' in data
        assert 'ViewAll=true' in data['url']
        assert data['api14'] == "30015286920000"
        assert 'note' in data
        assert 'browser interaction' in data['note']

        # Verify fetcher was called correctly
        mock_fetcher.get_combined_pdf_url.assert_called_once_with('30-015-28692')

    @patch('apps.public_core.views.nm_wells.NMDocumentFetcher')
    def test_get_combined_pdf_url_invalid_api(self, mock_fetcher_class, authenticated_client):
        """Test handling of invalid API format."""
        # Setup mock to raise ValueError
        mock_fetcher = MagicMock()
        mock_fetcher.__enter__ = Mock(return_value=mock_fetcher)
        mock_fetcher.__exit__ = Mock(return_value=False)
        mock_fetcher.get_combined_pdf_url = Mock(side_effect=ValueError("Invalid API number"))
        mock_fetcher_class.return_value = mock_fetcher

        # Make request
        url = reverse('nm_well_combined_pdf', kwargs={'api': 'invalid'})
        response = authenticated_client.get(url)

        # Assertions
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert 'detail' in data
        assert 'Invalid API number' in data['detail']

    @patch('apps.public_core.views.nm_wells.NMDocumentFetcher')
    def test_get_combined_pdf_url_different_api_formats(self, mock_fetcher_class, authenticated_client):
        """Test that different API formats are accepted."""
        # Setup mock
        mock_fetcher = MagicMock()
        mock_fetcher.__enter__ = Mock(return_value=mock_fetcher)
        mock_fetcher.__exit__ = Mock(return_value=False)
        mock_fetcher.get_combined_pdf_url = Mock(
            return_value="https://ocdimage.emnrd.nm.gov/imaging/WellFileView.aspx?RefType=WF&RefID=30015286920000&ViewAll=true"
        )
        mock_fetcher._api_to_api14 = Mock(return_value="30015286920000")
        mock_fetcher_class.return_value = mock_fetcher

        # Test different API formats
        api_formats = ['30-015-28692', '3001528692', '30015286920000']

        for api_format in api_formats:
            url = reverse('nm_well_combined_pdf', kwargs={'api': api_format})
            response = authenticated_client.get(url)
            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert 'url' in data
            assert data['api14'] == "30015286920000"


@pytest.mark.django_db
class TestEndToEndFlow:
    """Test end-to-end flow of NM well data retrieval."""

    @patch('apps.public_core.views.nm_wells.NMWellScraper')
    @patch('apps.public_core.views.nm_wells.NMDocumentFetcher')
    def test_complete_well_lookup_flow(
        self,
        mock_fetcher_class,
        mock_scraper_class,
        authenticated_client,
        sample_well_data,
        sample_documents
    ):
        """Test complete flow: get well data, list documents, get combined PDF URL."""
        # Setup scraper mock
        mock_scraper = MagicMock()
        mock_scraper.__enter__ = Mock(return_value=mock_scraper)
        mock_scraper.__exit__ = Mock(return_value=False)
        mock_scraper.fetch_well = Mock(return_value=sample_well_data)
        mock_scraper_class.return_value = mock_scraper

        # Setup fetcher mock
        mock_fetcher = MagicMock()
        mock_fetcher.__enter__ = Mock(return_value=mock_fetcher)
        mock_fetcher.__exit__ = Mock(return_value=False)
        mock_fetcher.list_documents = Mock(return_value=sample_documents)
        mock_fetcher.get_combined_pdf_url = Mock(
            return_value="https://ocdimage.emnrd.nm.gov/imaging/WellFileView.aspx?RefType=WF&RefID=30015286920000&ViewAll=true"
        )
        mock_fetcher._api_to_api14 = Mock(return_value="30015286920000")
        mock_fetcher_class.return_value = mock_fetcher

        api = '30-015-28692'

        # Step 1: Get well data
        url = reverse('nm_well_detail', kwargs={'api': api})
        response = authenticated_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        well_data = response.json()
        assert well_data['well_name'] == "FEDERAL 24-19 1H"

        # Step 2: List documents
        url = reverse('nm_well_documents', kwargs={'api': api})
        response = authenticated_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        docs_data = response.json()
        assert docs_data['count'] == 3

        # Step 3: Get combined PDF URL
        url = reverse('nm_well_combined_pdf', kwargs={'api': api})
        response = authenticated_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        pdf_data = response.json()
        assert 'url' in pdf_data
        assert pdf_data['api14'] == "30015286920000"


@pytest.mark.django_db
class TestAPIResponseFormat:
    """Test that API responses conform to expected formats."""

    @patch('apps.public_core.views.nm_wells.NMWellScraper')
    def test_well_data_response_structure(self, mock_scraper_class, authenticated_client, sample_well_data):
        """Test that well data response has all expected fields."""
        # Setup mock
        mock_scraper = MagicMock()
        mock_scraper.__enter__ = Mock(return_value=mock_scraper)
        mock_scraper.__exit__ = Mock(return_value=False)
        mock_scraper.fetch_well = Mock(return_value=sample_well_data)
        mock_scraper_class.return_value = mock_scraper

        # Make request
        url = reverse('nm_well_detail', kwargs={'api': '30-015-28692'})
        response = authenticated_client.get(url)

        # Check response structure
        data = response.json()
        expected_fields = [
            'api10', 'api14', 'well_name', 'operator_name', 'operator_number',
            'status', 'well_type', 'direction', 'surface_location',
            'latitude', 'longitude', 'elevation_ft', 'proposed_depth_ft',
            'tvd_ft', 'formation', 'spud_date', 'completion_date'
        ]

        for field in expected_fields:
            assert field in data, f"Missing field: {field}"

    @patch('apps.public_core.views.nm_wells.NMDocumentFetcher')
    def test_documents_response_structure(self, mock_fetcher_class, authenticated_client, sample_documents):
        """Test that documents response has expected structure."""
        # Setup mock
        mock_fetcher = MagicMock()
        mock_fetcher.__enter__ = Mock(return_value=mock_fetcher)
        mock_fetcher.__exit__ = Mock(return_value=False)
        mock_fetcher.list_documents = Mock(return_value=sample_documents)
        mock_fetcher_class.return_value = mock_fetcher

        # Make request
        url = reverse('nm_well_documents', kwargs={'api': '30-015-28692'})
        response = authenticated_client.get(url)

        # Check response structure
        data = response.json()
        assert 'api' in data
        assert 'count' in data
        assert 'documents' in data
        assert isinstance(data['documents'], list)

        # Check document structure
        for doc in data['documents']:
            assert 'filename' in doc
            assert 'url' in doc
            assert 'doc_type' in doc

    @patch('apps.public_core.views.nm_wells.NMDocumentFetcher')
    def test_combined_pdf_response_structure(self, mock_fetcher_class, authenticated_client):
        """Test that combined PDF response has expected structure."""
        # Setup mock
        mock_fetcher = MagicMock()
        mock_fetcher.__enter__ = Mock(return_value=mock_fetcher)
        mock_fetcher.__exit__ = Mock(return_value=False)
        mock_fetcher.get_combined_pdf_url = Mock(return_value="https://example.com/pdf")
        mock_fetcher._api_to_api14 = Mock(return_value="30015286920000")
        mock_fetcher_class.return_value = mock_fetcher

        # Make request
        url = reverse('nm_well_combined_pdf', kwargs={'api': '30-015-28692'})
        response = authenticated_client.get(url)

        # Check response structure
        data = response.json()
        assert 'url' in data
        assert 'api14' in data
        assert 'note' in data

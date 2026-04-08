"""
Tests for FilingSyncer — upsert logic, credential lookup, and well matching.

Strategy
--------
- ``test_sync_no_credentials`` — pure mock, no DB needed.
- ``test_sync_creates_new_filings``, ``test_sync_updates_changed_status``,
  ``test_sync_skips_unchanged``, ``test_sync_handles_no_well_match`` — use
  the real ORM (``@pytest.mark.django_db``) for FilingStatusRecord and
  WellRegistry.  Only Playwright and portal scraping are mocked.

All async tests use ``pytest.mark.asyncio``.  The FilingSyncer uses
``asyncio.get_event_loop().run_in_executor`` for ORM calls, which works
correctly in a standard pytest-asyncio event loop.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.intelligence.services.filing_syncer import FilingSyncer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_filing_data(
    filing_id: str = "RRC-2026-001",
    status: str = "pending",
    well_api: str | None = "42501705750000",
    remarks: str = "",
    form_type: str = "w3a",
) -> dict:
    """Build a minimal filing dict matching the BasePortalScraper contract."""
    return {
        "filing_id": filing_id,
        "form_type": form_type,
        "status": status,
        "portal_url": f"https://webapps.rrc.texas.gov/EWA/ewastatus.do?filingId={filing_id}",
        "status_date": "2026-03-15",
        "remarks": remarks,
        "reviewer_name": "Jane Smith",
        "well_api": well_api,
        "raw_data": {
            "filing_id": filing_id,
            "status": status,
        },
    }


def _make_mock_scraper(filings: list[dict]) -> MagicMock:
    """Return a mock scraper whose async methods return the given filings list."""
    scraper = MagicMock()
    scraper.authenticate = AsyncMock(return_value=MagicMock())
    scraper.scrape_filings_list = AsyncMock(return_value=filings)
    scraper.check_filing_status = AsyncMock()
    return scraper


def _make_mock_playwright_cm(browser: MagicMock) -> MagicMock:
    """Return a mock async context manager that yields a playwright-like object."""
    pw_mock = MagicMock()
    pw_mock.chromium.launch = AsyncMock(return_value=browser)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=pw_mock)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _make_mock_browser() -> MagicMock:
    context = MagicMock()
    context.new_page = AsyncMock(return_value=MagicMock())

    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()
    return browser


# ---------------------------------------------------------------------------
# 1. No credentials — early exit without touching the browser
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_no_credentials():
    """
    When PortalCredential.DoesNotExist is raised the syncer returns an error
    dict with error='no_credentials' and all counts set to zero.
    """
    syncer = FilingSyncer()
    tenant_id = str(uuid.uuid4())

    with patch(
        "apps.intelligence.services.filing_syncer.async_playwright"
    ) as mock_pw, patch(
        "apps.intelligence.models.PortalCredential.objects"
    ) as mock_mgr:
        from apps.intelligence.models import PortalCredential

        mock_mgr.get.side_effect = PortalCredential.DoesNotExist

        # run_in_executor calls the lambda in a thread; patch it to raise
        async def _fake_executor(executor, fn):
            raise PortalCredential.DoesNotExist()

        loop = asyncio.get_event_loop()
        with patch.object(loop, "run_in_executor", side_effect=_fake_executor):
            result = await syncer.sync_filings(tenant_id=tenant_id, agency="RRC")

    assert result["status"] == "error"
    assert result["error"] == "no_credentials"
    assert result["created"] == 0
    assert result["updated"] == 0
    assert result["unchanged"] == 0
    assert result["errors"] == 0
    # Browser must not have been opened
    mock_pw.assert_not_called()


# ---------------------------------------------------------------------------
# 2. New filings created (happy path)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_sync_creates_new_filings():
    """
    When the scraper returns 2 filings and neither exists in the DB yet,
    both should be created with source='synced' and summary shows created=2.
    """
    from apps.intelligence.models import FilingStatusRecord, PortalCredential
    from apps.public_core.models import WellRegistry

    tenant_id = uuid.uuid4()

    # Pre-create the well so FK can be satisfied
    well = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: WellRegistry.objects.create(
            api14="42501705750001",
            state="TX",
            county="Andrews",
            district="8A",
            operator_name="Test Op",
            field_name="Field A",
            lease_name="Lease A",
            well_number="1",
        ),
    )

    filings = [
        _make_filing_data(filing_id="RRC-NEW-001", well_api=well.api14),
        _make_filing_data(filing_id="RRC-NEW-002", well_api=well.api14),
    ]
    scraper = _make_mock_scraper(filings)
    browser = _make_mock_browser()

    credential = MagicMock(spec=PortalCredential)
    credential.id = uuid.uuid4()

    syncer = FilingSyncer()

    with patch(
        "apps.intelligence.services.filing_syncer.async_playwright",
        return_value=_make_mock_playwright_cm(browser),
    ), patch(
        "apps.intelligence.services.filing_syncer.get_scraper",
        return_value=scraper,
    ):
        # Patch run_in_executor only for credential fetch; let ORM calls run normally
        original_run_in_executor = asyncio.get_event_loop().run_in_executor

        call_count = 0

        async def selective_executor(executor, fn):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call is credential lookup — return mock credential
                return credential
            # All other calls (ORM queries/creates) run for real
            return await original_run_in_executor(executor, fn)

        loop = asyncio.get_event_loop()
        with patch.object(loop, "run_in_executor", side_effect=selective_executor):
            result = await syncer.sync_filings(
                tenant_id=str(tenant_id), agency="RRC"
            )

    assert result["status"] == "success"
    assert result["created"] == 2
    assert result["updated"] == 0
    assert result["unchanged"] == 0
    assert result["errors"] == 0

    records = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: list(
            FilingStatusRecord.objects.filter(tenant_id=tenant_id).order_by("filing_id")
        ),
    )
    assert len(records) == 2
    assert all(r.source == "synced" for r in records)
    filing_ids = {r.filing_id for r in records}
    assert filing_ids == {"RRC-NEW-001", "RRC-NEW-002"}

    # Cleanup
    await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: FilingStatusRecord.objects.filter(tenant_id=tenant_id).delete(),
    )
    await asyncio.get_event_loop().run_in_executor(None, well.delete)


# ---------------------------------------------------------------------------
# 3. Existing filing with changed status → updated
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_sync_updates_changed_status():
    """
    When the scraper returns a filing whose status differs from the existing
    DB record, the record should be updated and summary shows updated=1.
    """
    from apps.intelligence.models import FilingStatusRecord, PortalCredential
    from apps.public_core.models import WellRegistry

    tenant_id = uuid.uuid4()

    well = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: WellRegistry.objects.create(
            api14="42501705750002",
            state="TX",
            county="Andrews",
            district="8A",
            operator_name="Test Op",
            field_name="Field B",
            lease_name="Lease B",
            well_number="2",
        ),
    )

    # Pre-create existing record with 'pending' status
    existing = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: FilingStatusRecord.objects.create(
            filing_id="RRC-UPD-001",
            tenant_id=tenant_id,
            well=well,
            agency="RRC",
            form_type="w3a",
            status="pending",
            source="synced",
        ),
    )

    # Scraper returns the same filing but with status 'approved'
    filings = [_make_filing_data(filing_id="RRC-UPD-001", status="approved", well_api=well.api14)]
    scraper = _make_mock_scraper(filings)
    browser = _make_mock_browser()

    credential = MagicMock(spec=PortalCredential)
    credential.id = uuid.uuid4()

    syncer = FilingSyncer()

    with patch(
        "apps.intelligence.services.filing_syncer.async_playwright",
        return_value=_make_mock_playwright_cm(browser),
    ), patch(
        "apps.intelligence.services.filing_syncer.get_scraper",
        return_value=scraper,
    ):
        original_run_in_executor = asyncio.get_event_loop().run_in_executor
        call_count = 0

        async def selective_executor(executor, fn):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return credential
            return await original_run_in_executor(executor, fn)

        loop = asyncio.get_event_loop()
        with patch.object(loop, "run_in_executor", side_effect=selective_executor):
            result = await syncer.sync_filings(
                tenant_id=str(tenant_id), agency="RRC"
            )

    assert result["status"] == "success"
    assert result["updated"] == 1
    assert result["created"] == 0
    assert result["unchanged"] == 0

    existing.refresh_from_db()
    assert existing.status == "approved"

    # Cleanup
    await asyncio.get_event_loop().run_in_executor(None, existing.delete)
    await asyncio.get_event_loop().run_in_executor(None, well.delete)


# ---------------------------------------------------------------------------
# 4. Unchanged — same status, same remarks → no DB write
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_sync_skips_unchanged():
    """
    When the scraper returns a filing whose status and remarks match the
    existing record exactly, summary should show unchanged=1 and the record
    must not be mutated.
    """
    from apps.intelligence.models import FilingStatusRecord, PortalCredential
    from apps.public_core.models import WellRegistry

    tenant_id = uuid.uuid4()

    well = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: WellRegistry.objects.create(
            api14="42501705750003",
            state="TX",
            county="Andrews",
            district="8A",
            operator_name="Test Op",
            field_name="Field C",
            lease_name="Lease C",
            well_number="3",
        ),
    )

    existing = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: FilingStatusRecord.objects.create(
            filing_id="RRC-SAME-001",
            tenant_id=tenant_id,
            well=well,
            agency="RRC",
            form_type="w3a",
            status="approved",
            agency_remarks="",
            source="synced",
        ),
    )
    original_updated_at = existing.updated_at

    # Scraper returns same status and empty remarks
    filings = [_make_filing_data(filing_id="RRC-SAME-001", status="approved", remarks="", well_api=well.api14)]
    scraper = _make_mock_scraper(filings)
    browser = _make_mock_browser()

    credential = MagicMock(spec=PortalCredential)
    credential.id = uuid.uuid4()

    syncer = FilingSyncer()

    with patch(
        "apps.intelligence.services.filing_syncer.async_playwright",
        return_value=_make_mock_playwright_cm(browser),
    ), patch(
        "apps.intelligence.services.filing_syncer.get_scraper",
        return_value=scraper,
    ):
        original_run_in_executor = asyncio.get_event_loop().run_in_executor
        call_count = 0

        async def selective_executor(executor, fn):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return credential
            return await original_run_in_executor(executor, fn)

        loop = asyncio.get_event_loop()
        with patch.object(loop, "run_in_executor", side_effect=selective_executor):
            result = await syncer.sync_filings(
                tenant_id=str(tenant_id), agency="RRC"
            )

    assert result["status"] == "success"
    assert result["unchanged"] == 1
    assert result["updated"] == 0
    assert result["created"] == 0

    # Cleanup
    await asyncio.get_event_loop().run_in_executor(None, existing.delete)
    await asyncio.get_event_loop().run_in_executor(None, well.delete)


# ---------------------------------------------------------------------------
# 5. No well match → filing skipped (counted as error)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_sync_handles_no_well_match():
    """
    When the scraper returns a filing whose well_api does not match any
    WellRegistry row, the filing must be skipped and counted under errors.
    No FilingStatusRecord should be created.
    """
    from apps.intelligence.models import FilingStatusRecord, PortalCredential

    tenant_id = uuid.uuid4()

    # No WellRegistry entry for this API — intentionally absent
    filings = [_make_filing_data(filing_id="RRC-NOWL-001", well_api="99999999999999")]
    scraper = _make_mock_scraper(filings)
    browser = _make_mock_browser()

    credential = MagicMock(spec=PortalCredential)
    credential.id = uuid.uuid4()

    syncer = FilingSyncer()

    with patch(
        "apps.intelligence.services.filing_syncer.async_playwright",
        return_value=_make_mock_playwright_cm(browser),
    ), patch(
        "apps.intelligence.services.filing_syncer.get_scraper",
        return_value=scraper,
    ):
        original_run_in_executor = asyncio.get_event_loop().run_in_executor
        call_count = 0

        async def selective_executor(executor, fn):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return credential
            return await original_run_in_executor(executor, fn)

        loop = asyncio.get_event_loop()
        with patch.object(loop, "run_in_executor", side_effect=selective_executor):
            result = await syncer.sync_filings(
                tenant_id=str(tenant_id), agency="RRC"
            )

    assert result["status"] == "success"
    assert result["errors"] == 1
    assert result["created"] == 0
    assert result["updated"] == 0

    # No record should have been created
    count = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: FilingStatusRecord.objects.filter(
            filing_id="RRC-NOWL-001", tenant_id=tenant_id
        ).count(),
    )
    assert count == 0


# ---------------------------------------------------------------------------
# FilingSyncer._parse_date (static helper, no DB needed)
# ---------------------------------------------------------------------------


class TestFilingSyncerParseDate:
    def test_parse_valid_iso_date(self):
        from datetime import date

        result = FilingSyncer._parse_date("2026-03-15")
        assert result == date(2026, 3, 15)

    def test_parse_none_returns_none(self):
        assert FilingSyncer._parse_date(None) is None

    def test_parse_empty_string_returns_none(self):
        assert FilingSyncer._parse_date("") is None

    def test_parse_invalid_string_returns_none(self):
        assert FilingSyncer._parse_date("not-a-date") is None

    def test_parse_mm_dd_yyyy_returns_none(self):
        """The syncer's _parse_date only handles ISO strings; MM/DD/YYYY is the scraper's job."""
        result = FilingSyncer._parse_date("03/15/2026")
        assert result is None  # fromisoformat raises ValueError for this format

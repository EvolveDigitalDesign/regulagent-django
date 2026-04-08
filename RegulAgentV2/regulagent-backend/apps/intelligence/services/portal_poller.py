"""
PortalStatusPoller — polls agency portals for filing status updates.
PostSubmissionCapture — captures confirmation + creates initial FilingStatusRecord.
"""

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from playwright.async_api import async_playwright

if TYPE_CHECKING:
    from apps.intelligence.models import FilingStatusRecord, PortalCredential

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Statuses that trigger a rejection/deficiency record downstream.
# Imported by tasks_polling.py — must remain in this module.
# ---------------------------------------------------------------------------
ADVERSE_STATUSES = {"rejected", "revision_requested", "deficiency"}


class PortalStatusPoller:
    """
    Polls agency portals for filing status updates using Playwright browser automation.

    Dispatches authentication and status-checking to the appropriate concrete
    scraper via the portal_scrapers registry.

    Usage (sync, from Celery task):
        poller = PortalStatusPoller(agency='RRC')
        updates = async_to_sync(poller.poll_pending_filings)(tenant_id)
    """

    def __init__(self, agency: str = "RRC"):
        self.agency = agency

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def poll_pending_filings(self, tenant_id: str) -> list[dict]:
        """
        Poll portal for all pending/under_review filings for a tenant.

        Steps:
        1. Fetch PortalCredential for tenant + agency
        2. Resolve scraper from registry and authenticate
        3. For each pending FilingStatusRecord: look up by filing_id
        4. Scrape current status, remarks, reviewer, date via scraper
        5. Return list of status-update dicts

        Returns list of dicts with keys:
            filing_status_id, filing_id, old_status, new_status,
            remarks, reviewer_name, status_date, raw_data
        """
        from apps.intelligence.models import FilingStatusRecord, PortalCredential
        from apps.intelligence.services.portal_scrapers import get_scraper

        updates = []

        # Fetch credential
        try:
            credential = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: PortalCredential.objects.get(
                    tenant_id=tenant_id,
                    agency=self.agency,
                    is_active=True,
                ),
            )
        except PortalCredential.DoesNotExist:
            logger.warning(
                "No active PortalCredential for tenant=%s agency=%s — skipping",
                tenant_id,
                self.agency,
            )
            return updates

        # Fetch pending + under_review filings for this tenant
        pending_filings = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: list(
                FilingStatusRecord.objects.filter(
                    tenant_id=tenant_id,
                    agency=self.agency,
                    status__in=["pending", "under_review"],
                ).only("id", "filing_id", "status")
            ),
        )

        if not pending_filings:
            logger.info(
                "No pending filings for tenant=%s agency=%s", tenant_id, self.agency
            )
            return updates

        # Resolve scraper — raises KeyError if agency is not registered
        scraper = get_scraper(self.agency)

        # Run browser session
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                page = await scraper.authenticate(credential, context)

                for filing in pending_filings:
                    try:
                        status_data = await scraper.check_filing_status(
                            page, filing.filing_id
                        )

                        updates.append(
                            {
                                "filing_status_id": str(filing.id),
                                "filing_id": filing.filing_id,
                                "old_status": filing.status,
                                **status_data,
                            }
                        )

                    except Exception as exc:
                        logger.exception(
                            "Error checking filing %s for tenant=%s: %s",
                            filing.filing_id,
                            tenant_id,
                            exc,
                        )
                        # Continue — don't crash the whole batch for one filing

            finally:
                await browser.close()

        return updates


# ---------------------------------------------------------------------------
# PostSubmissionCapture
# ---------------------------------------------------------------------------


class PostSubmissionCapture:
    """
    Captures confirmation number and creates initial FilingStatusRecord
    immediately after RRCFormAutomator.submit_form() succeeds.
    """

    # Patterns that appear on RRC confirmation pages
    RRC_CONFIRMATION_PATTERNS = [
        r"filing number[:\s#]*([A-Z0-9\-]+)",
        r"confirmation[:\s#]*([A-Z0-9\-]+)",
        r"tracking number[:\s#]*([A-Z0-9\-]+)",
        r"filing id[:\s#]*([A-Z0-9\-]+)",
        r"reference number[:\s#]*([A-Z0-9\-]+)",
        r"your filing[:\s#]*([A-Z0-9\-]+)",
    ]

    @staticmethod
    def capture_rrc_confirmation(page_content: str, form_type: str) -> dict:
        """
        Parse RRC confirmation page HTML/text for filing_id (tracking number).

        Returns:
            {
                filing_id: str | None,
                status: str,           # 'pending' initially
                confirmation_text: str,
            }
        """
        filing_id = None

        for pattern in PostSubmissionCapture.RRC_CONFIRMATION_PATTERNS:
            match = re.search(pattern, page_content, re.IGNORECASE)
            if match:
                filing_id = match.group(1).strip()
                logger.info(
                    "Captured RRC confirmation number %s for form_type=%s",
                    filing_id,
                    form_type,
                )
                break

        if not filing_id:
            logger.warning(
                "Could not extract RRC confirmation/tracking number from page. "
                "form_type=%s. Content snippet: %s",
                form_type,
                page_content[:500],
            )

        # Extract a meaningful snippet of confirmation text for the record
        confirmation_text = page_content[:1000] if page_content else ""

        return {
            "filing_id": filing_id,
            "status": "pending",
            "confirmation_text": confirmation_text,
        }

    @staticmethod
    def create_initial_filing_status(
        filing_id: str,
        form_type: str,
        agency: str,
        tenant_id: str,
        well_id: str,
        w3_form_id=None,
        plan_snapshot_id=None,
        c103_form_id=None,
        state: str = "",
        district: str = "",
        county: str = "",
    ) -> "FilingStatusRecord":
        """
        Create initial FilingStatusRecord(status='pending') after submission.

        Args:
            filing_id: Agency tracking/confirmation number.
            form_type: One of FORM_TYPE_CHOICES keys (e.g. 'w3', 'w3a', 'c103').
            agency: One of AGENCY_CHOICES keys (e.g. 'RRC').
            tenant_id: UUID string of the owning tenant.
            well_id: PK of the related WellRegistry.
            w3_form_id: Optional PK of W3FormORM.
            plan_snapshot_id: Optional PK of PlanSnapshot.
            c103_form_id: Optional PK of C103FormORM.
            state: Two-letter state code.
            district: Agency district code.
            county: County name.

        Returns:
            Newly created FilingStatusRecord instance.
        """
        from apps.intelligence.models import FilingStatusRecord

        record = FilingStatusRecord.objects.create(
            filing_id=filing_id,
            tenant_id=tenant_id,
            well_id=well_id,
            agency=agency,
            form_type=form_type,
            status="pending",
            w3_form_id=w3_form_id,
            plan_snapshot_id=plan_snapshot_id,
            c103_form_id=c103_form_id,
            state=state,
            district=district,
            county=county,
        )

        logger.info(
            "Created FilingStatusRecord id=%s filing_id=%s agency=%s form_type=%s",
            record.id,
            filing_id,
            agency,
            form_type,
        )

        return record

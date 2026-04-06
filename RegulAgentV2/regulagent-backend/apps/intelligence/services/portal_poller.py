"""
PortalStatusPoller — polls agency portals for filing status updates.
PostSubmissionCapture — captures confirmation + creates initial FilingStatusRecord.
"""

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from asgiref.sync import async_to_sync
from django.utils import timezone
from playwright.async_api import async_playwright, Page

if TYPE_CHECKING:
    from apps.intelligence.models import FilingStatusRecord, PortalCredential

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RRC portal constants
# ---------------------------------------------------------------------------

RRC_LOGIN_URL = "https://webapps.rrc.texas.gov/security/login.do"
RRC_STATUS_SEARCH_URL = (
    "https://webapps.rrc.texas.gov/EWA/ewastatus.do"
)

# Statuses that trigger a rejection/deficiency record downstream
ADVERSE_STATUSES = {"rejected", "revision_requested", "deficiency"}

# Map portal status strings to internal FilingStatusRecord choices
RRC_STATUS_MAP = {
    "pending": "pending",
    "under review": "under_review",
    "approved": "approved",
    "rejected": "rejected",
    "revision requested": "revision_requested",
    "deficiency": "deficiency",
    "deficiency notice": "deficiency",
}


class PortalStatusPoller:
    """
    Polls agency portals for filing status updates using Playwright browser automation.

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
        2. Authenticate to portal
        3. For each pending FilingStatusRecord: look up by filing_id
        4. Scrape current status, remarks, reviewer, date
        5. Return list of status-update dicts

        Returns list of dicts with keys:
            filing_status_id, filing_id, old_status, new_status,
            remarks, reviewer_name, status_date, raw_data
        """
        from apps.intelligence.models import FilingStatusRecord, PortalCredential

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

        # Run browser session
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context()

                if self.agency == "RRC":
                    page = await self._authenticate_rrc(credential, context)
                elif self.agency == "NMOCD":
                    page = await self._authenticate_nmocd(credential, context)
                else:
                    logger.error("Unknown agency: %s", self.agency)
                    return updates

                for filing in pending_filings:
                    try:
                        if self.agency == "RRC":
                            status_data = await self._check_filing_status_rrc(
                                page, filing.filing_id
                            )
                        elif self.agency == "NMOCD":
                            status_data = await self._check_filing_status_nmocd(
                                page, filing.filing_id
                            )
                        else:
                            continue

                        updates.append(
                            {
                                "filing_status_id": str(filing.id),
                                "filing_id": filing.filing_id,
                                "old_status": filing.status,
                                **status_data,
                            }
                        )

                    except NotImplementedError:
                        raise
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

    # ------------------------------------------------------------------
    # RRC portal methods
    # ------------------------------------------------------------------

    async def _authenticate_rrc(self, credential: "PortalCredential", context) -> Page:
        """
        Authenticate to RRC portal.
        URL: webapps.rrc.texas.gov/security/login.do

        Reuses the same login flow as RRCFormAutomator.authenticate().
        Returns an authenticated Page.
        """
        username = await asyncio.get_event_loop().run_in_executor(
            None, credential.get_username
        )
        password = await asyncio.get_event_loop().run_in_executor(
            None, credential.get_password
        )

        page = await context.new_page()
        await page.goto(RRC_LOGIN_URL)
        await page.wait_for_load_state("networkidle")

        logger.info("Starting RRC portal authentication for poller")

        # Fill credentials via JS (mirrors RRCFormAutomator.authenticate)
        await page.evaluate(
            f"""
            document.querySelector('input[name="login"]').value = '{username}';
            document.querySelector('input[name="password"]').value = '{password}';
            document.querySelector('input[name="login"]').dispatchEvent(new Event('input', {{ bubbles: true }}));
            document.querySelector('input[name="password"]').dispatchEvent(new Event('input', {{ bubbles: true }}));
            """
        )

        # Submit
        submit_btn = page.locator('input[type="submit"], button[type="submit"]').first
        await submit_btn.click()
        await page.wait_for_load_state("networkidle", timeout=15000)

        # Verify success: RRC shows a nav dropdown post-login
        nav_dropdown = await page.query_selector('select[name="go"]')
        if not nav_dropdown:
            error_el = await page.query_selector('.error, .alert-danger, [class*="error"]')
            error_msg = ""
            if error_el:
                error_msg = await error_el.inner_text()
            raise RuntimeError(
                f"RRC portal authentication failed for tenant credential {credential.id}. "
                f"Portal message: {error_msg or 'unknown'}"
            )

        # Update last_successful_login
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: (
                type(credential).objects.filter(pk=credential.pk).update(
                    last_successful_login=timezone.now()
                )
            ),
        )

        logger.info("RRC portal authentication successful (poller)")
        return page

    async def _check_filing_status_rrc(self, page: Page, filing_id: str) -> dict:
        """
        Check status of a specific filing on RRC portal.

        Navigates to the e-filing status search page and queries by filing_id
        (the agency tracking/confirmation number).

        Returns:
            {
                new_status: str,   # one of FILING_STATUS_CHOICES keys
                remarks: str,
                reviewer_name: str,
                status_date: str | None,   # ISO date string or None
                raw_data: dict,
            }
        """
        logger.debug("Checking RRC status for filing_id=%s", filing_id)

        # Navigate to status search
        await page.goto(RRC_STATUS_SEARCH_URL)
        await page.wait_for_load_state("networkidle", timeout=15000)

        # Attempt to find a search field for filing/tracking number
        search_field = await page.query_selector(
            'input[name="filingId"], input[name="trackingNo"], input[name="confirmationNo"], '
            'input[name="searchValue"], input[id*="filing"], input[id*="tracking"]'
        )

        raw_data: dict = {"filing_id": filing_id, "url": page.url}

        if search_field:
            await search_field.fill(filing_id)
            # Submit search
            submit_btn = await page.query_selector(
                'input[type="submit"], button[type="submit"], button[id*="search"]'
            )
            if submit_btn:
                await submit_btn.click()
                await page.wait_for_load_state("networkidle", timeout=15000)
            raw_data["page_content_snippet"] = (await page.content())[:2000]
        else:
            # No search form found — capture page content for debugging
            logger.warning(
                "No search field found on RRC status page (url=%s). "
                "Capturing page content for debug.",
                page.url,
            )
            raw_data["page_content_snippet"] = (await page.content())[:2000]

        # Parse status from page
        page_text = await page.inner_text("body")
        new_status = self._parse_rrc_status(page_text)
        remarks = self._parse_rrc_remarks(page_text)
        reviewer_name = self._parse_rrc_reviewer(page_text)
        status_date = self._parse_rrc_date(page_text)

        raw_data["parsed_text_snippet"] = page_text[:1000]

        return {
            "new_status": new_status,
            "remarks": remarks,
            "reviewer_name": reviewer_name,
            "status_date": status_date,
            "raw_data": raw_data,
        }

    # ------------------------------------------------------------------
    # NMOCD stubs
    # ------------------------------------------------------------------

    async def _authenticate_nmocd(
        self, credential: "PortalCredential", context
    ) -> Page:
        """NMOCD portal auth — stub for future implementation."""
        raise NotImplementedError("NMOCD polling not yet implemented")

    async def _check_filing_status_nmocd(
        self, page: Page, filing_id: str
    ) -> dict:
        """NMOCD filing status check — stub for future."""
        raise NotImplementedError("NMOCD polling not yet implemented")

    # ------------------------------------------------------------------
    # RRC page parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_rrc_status(page_text: str) -> str:
        """
        Map RRC portal status text to internal FilingStatusRecord status key.
        Falls back to 'under_review' when no known status is detected.
        """
        text_lower = page_text.lower()
        for portal_label, internal_key in RRC_STATUS_MAP.items():
            if portal_label in text_lower:
                return internal_key
        return "under_review"

    @staticmethod
    def _parse_rrc_remarks(page_text: str) -> str:
        """Extract remarks/notes from RRC portal page text."""
        patterns = [
            r"remarks?[:\s]+([^\n]+)",
            r"notes?[:\s]+([^\n]+)",
            r"comments?[:\s]+([^\n]+)",
            r"reason[:\s]+([^\n]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, page_text, re.IGNORECASE)
            if match:
                return match.group(1).strip()[:500]
        return ""

    @staticmethod
    def _parse_rrc_reviewer(page_text: str) -> str:
        """Extract reviewer name from RRC portal page text."""
        patterns = [
            r"reviewer?[:\s]+([A-Za-z ,\.]+)",
            r"reviewed by[:\s]+([A-Za-z ,\.]+)",
            r"assigned to[:\s]+([A-Za-z ,\.]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, page_text, re.IGNORECASE)
            if match:
                return match.group(1).strip()[:128]
        return ""

    @staticmethod
    def _parse_rrc_date(page_text: str) -> str | None:
        """
        Extract status date from RRC portal page text.
        Returns ISO date string (YYYY-MM-DD) or None.
        """
        # Match MM/DD/YYYY or YYYY-MM-DD
        patterns = [
            r"\b(\d{2}/\d{2}/\d{4})\b",
            r"\b(\d{4}-\d{2}-\d{2})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, page_text)
            if match:
                raw = match.group(1)
                if "/" in raw:
                    parts = raw.split("/")
                    try:
                        return f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
                    except IndexError:
                        pass
                return raw
        return None


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

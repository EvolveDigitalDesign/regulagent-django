"""
Base abstraction for agency portal scrapers.

Each state agency scraper must subclass BasePortalScraper and implement
all abstract methods. This interface is intentionally thin — it defines
the contract without prescribing implementation details.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from playwright.async_api import BrowserContext, Page

if TYPE_CHECKING:
    from apps.intelligence.models import PortalCredential


class BasePortalScraper(ABC):
    """
    Abstract base class for agency portal scrapers.

    Subclasses represent a single agency (e.g. RRC, NMOCD) and are
    responsible for authenticating with that agency's web portal,
    enumerating all filings visible to the authenticated user, and
    checking the status of individual filings.

    Usage pattern
    -------------
    scraper = RRCScraper()
    page    = await scraper.authenticate(credential, context)
    filings = await scraper.scrape_filings_list(page)
    status  = await scraper.check_filing_status(page, filing_id)
    """

    # Every concrete subclass MUST declare this as a short, uppercase
    # agency code (e.g. "RRC", "NMOCD").  The registry uses this value
    # as the lookup key.
    agency: str

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    @abstractmethod
    async def authenticate(
        self,
        credential: "PortalCredential",
        context: BrowserContext,
    ) -> Page:
        """
        Log into the agency portal using the supplied credential.

        Opens a new page within the provided Playwright BrowserContext,
        performs whatever login steps the portal requires (form submit,
        MFA, session cookie exchange, etc.), and returns the Page in an
        authenticated state ready for further scraping.

        Parameters
        ----------
        credential:
            A PortalCredential instance that carries the username,
            encrypted password, and any agency-specific config needed
            to authenticate.
        context:
            An active Playwright BrowserContext.  Callers own the
            context lifecycle; the scraper must NOT close it.

        Returns
        -------
        Page
            An authenticated Playwright Page.  The caller is responsible
            for closing it when done.

        Raises
        ------
        Exception
            Any exception that signals a login failure (bad credentials,
            portal unavailable, etc.) should propagate without being
            swallowed here.
        """

    # ------------------------------------------------------------------
    # Filing enumeration
    # ------------------------------------------------------------------

    @abstractmethod
    async def scrape_filings_list(self, page: Page) -> list[dict]:
        """
        Scrape all filings visible in the authenticated user's portal.

        The Page passed in must already be authenticated (i.e. returned
        by ``authenticate``).  The implementation may navigate across
        multiple pages / tabs to collect all records.

        Parameters
        ----------
        page:
            An authenticated Playwright Page.

        Returns
        -------
        list[dict]
            One dict per filing.  Each dict MUST contain the following
            keys (values may be ``None`` when the portal does not expose
            them):

            filing_id     (str)  — Portal-assigned unique identifier.
            form_type     (str)  — E.g. "W-3", "Sundry", "P-5".
            status        (str)  — Current status string from the portal.
            portal_url    (str)  — Direct URL to the filing detail page.
            status_date   (str)  — ISO-8601 date of the most recent status
                                   change, or the raw string if unparsed.
            remarks       (str)  — Free-text remarks from the agency.
            reviewer_name (str)  — Name of the assigned reviewer / analyst.
            well_api      (str)  — Associated API-14 well number, if any.
            raw_data      (dict) — All additional portal fields captured,
                                   keyed by their portal label.  Useful for
                                   debugging and future-proofing.
        """

    # ------------------------------------------------------------------
    # Single-filing status check
    # ------------------------------------------------------------------

    @abstractmethod
    async def check_filing_status(self, page: Page, filing_id: str) -> dict:
        """
        Check the current status of a single filing.

        Navigates to the filing's detail page and extracts the latest
        status information.  Useful for targeted refreshes without
        re-scraping the entire filings list.

        Parameters
        ----------
        page:
            An authenticated Playwright Page.
        filing_id:
            The portal-assigned identifier for the filing to check.

        Returns
        -------
        dict
            A dict with the following keys (values may be ``None``):

            new_status    (str)  — Current status string from the portal.
            remarks       (str)  — Free-text remarks from the agency.
            reviewer_name (str)  — Name of the assigned reviewer / analyst.
            status_date   (str)  — ISO-8601 date of the most recent status
                                   change, or the raw string if unparsed.
            raw_data      (dict) — All additional portal fields captured.
        """

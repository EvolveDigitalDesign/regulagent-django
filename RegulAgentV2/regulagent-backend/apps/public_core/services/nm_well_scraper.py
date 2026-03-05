"""
NM OCD Well Data Scraper

Scrapes well information from NM OCD Permitting portal.
No API available - must parse HTML.

Extracts data from all sections:
- General Well Information
- Event Dates (APD, spud, inspections, etc.)
- Casing/Strings table
- Well Completions (perforations, production method, etc.)
"""
import os
import re
import logging
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
import requests
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)


@dataclass
class CasingRecord:
    """Single casing/hole string entry from the NM OCD casing table."""
    string_type: str  # e.g., "Surface Casing", "Intermediate 1 Casing", "Production Casing", "Tubing", "Packer"
    taper: Optional[int] = None
    date_set: Optional[str] = None
    diameter_in: Optional[float] = None  # Hole/casing diameter in inches
    top_ft: Optional[float] = None
    bottom_ft: Optional[float] = None
    grade: Optional[str] = None
    length_ft: Optional[float] = None
    weight_ppf: Optional[float] = None  # Pounds per foot
    cement_bottom_ft: Optional[float] = None
    cement_top_ft: Optional[float] = None
    cement_method: Optional[str] = None
    cement_class: Optional[str] = None  # e.g., "Class C"
    cement_sacks: Optional[int] = None
    pressure_test: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PerforationInterval:
    """Perforation interval from well completions."""
    top_md_ft: Optional[float] = None  # Measured depth
    bottom_md_ft: Optional[float] = None
    top_vd_ft: Optional[float] = None  # Vertical depth (may be 0 for vertical wells)
    bottom_vd_ft: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FormationTop:
    """Formation top entry from the NM OCD formation tops table."""
    formation_name: str
    top_ft: Optional[float] = None
    producing: Optional[bool] = None
    method_obtained: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CompletionRecord:
    """Well completion details from NM OCD."""
    completion_id: Optional[str] = None
    completion_name: Optional[str] = None  # e.g., "SAND TANK; MORROW (GAS)"
    status: Optional[str] = None
    last_produced: Optional[str] = None
    bottomhole_location: Optional[str] = None
    acreage: Optional[str] = None
    production_method: Optional[str] = None
    # Well test data
    flowing_tubing_pressure_psi: Optional[float] = None
    choke_size_in: Optional[float] = None
    gas_volume_mcf: Optional[float] = None
    gas_oil_ratio: Optional[float] = None
    oil_volume_bbls: Optional[float] = None
    water_volume_bbls: Optional[float] = None
    # Completion event dates
    initial_effective_date: Optional[str] = None
    most_recent_approval: Optional[str] = None
    ready_to_produce_date: Optional[str] = None
    c104_approval_date: Optional[str] = None
    # Perforations for this completion
    perforations: List[PerforationInterval] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result['perforations'] = [p.to_dict() if hasattr(p, 'to_dict') else p for p in self.perforations]
        return result


@dataclass
class EventDates:
    """All event dates from the NM OCD well record."""
    # APD Dates
    initial_apd_approval: Optional[str] = None
    most_recent_apd_approval: Optional[str] = None
    current_apd_expiration: Optional[str] = None
    # Operational dates
    spud_date: Optional[str] = None
    completion_date: Optional[str] = None
    first_production_date: Optional[str] = None
    # Inspection dates
    last_inspection: Optional[str] = None
    last_mit_bht: Optional[str] = None  # Mechanical Integrity Test / Bottom Hole Test
    # Plugging dates
    plugging_date: Optional[str] = None
    ta_date: Optional[str] = None  # Temporarily Abandoned date

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NMWellData:
    """Structured NM well data from scraping."""
    # Identifiers
    api10: str  # xx-xxx-xxxxx format
    api14: str  # 14 digits no dashes
    well_name: str
    well_number: Optional[str] = None  # Extracted from well name after #

    # Operator
    operator_name: str = ""
    operator_number: str = ""

    # Well Classification
    status: str = ""
    well_type: str = ""  # OIL, GAS, etc.
    work_type: str = ""  # New, Recompletion, etc.
    direction: str = ""  # Vertical, Horizontal, Directional
    multi_lateral: Optional[bool] = None

    # Ownership
    mineral_owner: str = ""
    surface_owner: str = ""

    # Location
    surface_location: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    datum: str = ""  # NAD83, NAD27, etc.

    # Elevations
    gl_elevation_ft: Optional[float] = None  # Ground Level
    kb_elevation_ft: Optional[float] = None  # Kelly Bushing
    df_elevation_ft: Optional[float] = None  # Derrick Floor

    # Completion type
    sing_mult_compl: str = ""  # Single, Multiple
    potash_waiver: Optional[bool] = None

    # Formation
    formation: str = ""
    proposed_formation: str = ""

    # Depths
    proposed_depth_ft: Optional[int] = None
    measured_vertical_depth_ft: Optional[int] = None
    true_vertical_depth_ft: Optional[int] = None
    plugback_measured_ft: Optional[int] = None

    # Legacy depth fields (for backward compatibility)
    elevation_ft: Optional[float] = None  # Maps to gl_elevation_ft
    tvd_ft: Optional[int] = None  # Maps to true_vertical_depth_ft

    # Event dates
    event_dates: Optional[EventDates] = None
    spud_date: Optional[str] = None  # Legacy field
    completion_date: Optional[str] = None  # Legacy field

    # Casing records
    casing_records: List[CasingRecord] = field(default_factory=list)

    # Well completions
    completions: List[CompletionRecord] = field(default_factory=list)

    # Formation tops
    formation_tops: List[FormationTop] = field(default_factory=list)

    # Raw data for debugging
    raw_html: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert dataclass to dictionary."""
        result = asdict(self)
        # Handle nested dataclasses
        if self.event_dates:
            result['event_dates'] = self.event_dates.to_dict() if hasattr(self.event_dates, 'to_dict') else self.event_dates
        result['casing_records'] = [c.to_dict() if hasattr(c, 'to_dict') else c for c in self.casing_records]
        result['completions'] = [c.to_dict() if hasattr(c, 'to_dict') else c for c in self.completions]
        result['formation_tops'] = [f.to_dict() if hasattr(f, 'to_dict') else f for f in self.formation_tops]
        return result


class NMWellScraper:
    """Scraper for NM OCD well data."""

    BASE_URL = "https://wwwapps.emnrd.nm.gov/OCD/OCDPermitting/Data/WellDetails.aspx"

    # Markers that indicate a Cloudflare Turnstile challenge is present
    _TURNSTILE_MARKERS = ['cf-turnstile', 'turnstile', 'hfTurnstileToken', 'challenges.cloudflare.com/turnstile']

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.session = requests.Session()
        # Set a user agent to avoid blocking
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

    def _has_turnstile(self, html: str) -> bool:
        """Check if the HTML response contains a Cloudflare Turnstile challenge."""
        html_lower = html.lower()
        return any(marker in html_lower for marker in self._TURNSTILE_MARKERS)

    def _extract_turnstile_sitekey(self, html: str) -> Optional[str]:
        """Extract Cloudflare Turnstile sitekey from HTML."""
        soup = BeautifulSoup(html, 'html.parser')
        # Look for data-sitekey on cf-turnstile div
        turnstile_div = soup.find(attrs={'class': 'cf-turnstile'})
        if turnstile_div and turnstile_div.get('data-sitekey'):
            return turnstile_div['data-sitekey']
        # Fallback: regex for sitekey in turnstile.render() or data-sitekey attrs
        sitekey_match = re.search(r'data-sitekey=["\']([0-9a-zA-Z_-]+)["\']', html)
        if sitekey_match:
            return sitekey_match.group(1)
        # Also check for sitekey in JS turnstile.render calls
        render_match = re.search(r'turnstile\.render\([^)]*sitekey["\s:]+["\']([0-9a-zA-Z_-]+)', html)
        if render_match:
            return render_match.group(1)
        return None

    def _solve_turnstile(self, url: str, sitekey: str) -> str:
        """
        Solve Cloudflare Turnstile using 2captcha service.

        Args:
            url: The page URL where Turnstile appears
            sitekey: The Turnstile sitekey

        Returns:
            Solved Turnstile token string

        Raises:
            RuntimeError: If TWOCAPTCHA_API_KEY is not configured or solving fails
        """
        api_key = os.getenv('TWOCAPTCHA_API_KEY', '')
        if not api_key:
            raise RuntimeError(
                "TWOCAPTCHA_API_KEY environment variable is required to access NM OCD. "
                "EMNRD has added Cloudflare Turnstile protection to the permitting portal."
            )

        from twocaptcha import TwoCaptcha
        solver = TwoCaptcha(api_key)

        logger.info(f"Solving Turnstile for {url} (sitekey={sitekey[:8]}...)")
        try:
            result = solver.turnstile(sitekey=sitekey, url=url)
            token = result['code']
            logger.info("Turnstile solved successfully")
            return token
        except Exception as e:
            raise RuntimeError(f"Failed to solve Turnstile via 2captcha: {e}") from e

    def _fetch_with_turnstile(self, url: str, html: str) -> str:
        """
        Use Playwright to load the page, solve the Turnstile challenge via
        2captcha, inject the token, and trigger the page's own callback
        to submit the form and load the well data.

        Args:
            url: The well details URL
            html: The initial HTML response (used for sitekey extraction fallback)

        Returns:
            HTML string of the page after Turnstile submission
        """
        # Extract sitekey from the static HTML first (avoids waiting for Playwright)
        sitekey = self._extract_turnstile_sitekey(html)
        if not sitekey:
            raise RuntimeError(
                "Cloudflare Turnstile detected on NM OCD page but could not "
                "extract sitekey. The page structure may have changed."
            )

        # Solve the Turnstile challenge while Playwright loads
        token = self._solve_turnstile(url, sitekey)

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            try:
                # Use domcontentloaded — networkidle hangs due to Turnstile polling
                page.goto(url, wait_until='domcontentloaded', timeout=int(self.timeout * 1000))

                # Wait briefly for the ASP.NET form to be in the DOM
                page.wait_for_selector('#hfTurnstileToken', state='attached', timeout=10000)

                # Inject the solved token and invoke the page's callback
                # onTurnstileSuccess sets hfTurnstileToken and submits the form
                page.evaluate("""(token) => {
                    const hf = document.getElementById('hfTurnstileToken');
                    if (hf) { hf.value = token; }
                    // Also populate standard response fields
                    document.querySelectorAll(
                        '[name="cf-turnstile-response"], [name="g-recaptcha-response"]'
                    ).forEach(el => { el.value = token; });
                }""", token)

                # Use expect_navigation to catch the form submit navigation
                with page.expect_navigation(wait_until='domcontentloaded', timeout=int(self.timeout * 1000)):
                    page.evaluate("""(token) => {
                        if (typeof onTurnstileSuccess === 'function') {
                            onTurnstileSuccess(token);
                        } else {
                            const form = document.getElementById('aspnetForm')
                                      || document.getElementById('form1');
                            if (form) { form.submit(); }
                        }
                    }""", token)

                result_html = page.content()
                return result_html

            finally:
                browser.close()

    def fetch_well(self, api: str, include_raw_html: bool = False) -> NMWellData:
        """
        Fetch and parse well data for given API number.

        If the page returns a Cloudflare Turnstile challenge, solves it via
        2captcha and uses Playwright to submit the token.

        Args:
            api: API number in any format (will be normalized)
            include_raw_html: If True, includes raw HTML in response for debugging

        Returns:
            NMWellData with extracted fields

        Raises:
            ValueError: If API format invalid
            requests.RequestException: If HTTP request fails
            RuntimeError: If Turnstile is present but TWOCAPTCHA_API_KEY is not set
        """
        api10 = self._normalize_api10(api)
        url = f"{self.BASE_URL}?api={api10}"

        logger.info(f"Fetching NM well data: {url}")

        # First try a simple GET - may succeed if no CAPTCHA
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        html = response.text

        # Check if Cloudflare Turnstile is blocking the page
        if self._has_turnstile(html):
            logger.warning("Cloudflare Turnstile detected on NM OCD page, solving via 2captcha...")
            html = self._fetch_with_turnstile(url, html)

            # Verify Turnstile was bypassed (well data should now be present)
            if self._has_turnstile(html) and 'WellDetails' not in html:
                raise RuntimeError(
                    "Cloudflare Turnstile still present after solving. "
                    "The token may have expired or the submission method needs updating."
                )

        return self._parse_html(html, api10, include_raw_html)

    def _normalize_api10(self, api: str) -> str:
        """
        Convert API to xx-xxx-xxxxx format for NM.

        NM uses 10-digit API: state(2)-county(3)-sequence(5)
        Example: 30-015-28692
        """
        digits = re.sub(r'[^0-9]', '', str(api))

        # If 14 digits (full API), extract first 10
        if len(digits) == 14:
            digits = digits[:10]

        # Must be exactly 10 digits
        if len(digits) != 10:
            raise ValueError(
                f"Invalid NM API number: {api}. "
                f"Expected 10 digits, got {len(digits)}."
            )

        # Validate state code (NM = 30)
        state_code = digits[:2]
        if state_code != '30':
            raise ValueError(
                f"Invalid NM API number: {api}. "
                f"State code must be 30 (New Mexico), got {state_code}."
            )

        return f"{digits[:2]}-{digits[2:5]}-{digits[5:10]}"

    def _api10_to_api14(self, api10: str) -> str:
        """Convert API-10 to API-14 format."""
        digits = api10.replace("-", "")
        return digits + "0000"

    def _parse_html(self, html: str, api10: str, include_raw_html: bool = False) -> NMWellData:
        """Parse HTML and extract well data from all sections."""
        soup = BeautifulSoup(html, 'html.parser')

        # Extract all text content for easier searching
        full_text = soup.get_text()

        # Extract well name and number from header
        well_name, well_number = self._extract_well_name_and_number(soup, full_text)

        # Extract event dates
        event_dates = self._extract_event_dates(soup, full_text)

        # Extract casing records
        casing_records = self._extract_casing_records(soup)

        # Extract completions and perforations
        completions = self._extract_completions(soup, full_text)

        # Extract formation tops
        formation_tops = self._extract_formation_tops(soup)

        # Extract coordinates and datum
        lat, lon, datum = self._extract_coordinates(full_text)

        # Extract multi-lateral and potash waiver booleans
        multi_lateral = self._extract_boolean(full_text, 'Multi-Lateral', 'Multi Lateral')
        potash_waiver = self._extract_boolean(full_text, 'Potash Waiver')

        # Build the data object
        data = NMWellData(
            api10=api10,
            api14=self._api10_to_api14(api10),
            well_name=well_name,
            well_number=well_number,
            operator_name=self._extract_operator_name(soup, full_text),
            operator_number=self._extract_operator_number(soup, full_text),
            status=self._extract_field(soup, full_text, 'Status'),
            well_type=self._extract_field(soup, full_text, 'Well Type'),
            work_type=self._extract_field(soup, full_text, 'Work Type'),
            direction=self._extract_field(soup, full_text, 'Direction'),
            multi_lateral=multi_lateral,
            mineral_owner=self._extract_field(soup, full_text, 'Mineral Owner'),
            surface_owner=self._extract_field(soup, full_text, 'Surface Owner'),
            surface_location=self._extract_field(soup, full_text, 'Surface Location'),
            latitude=lat,
            longitude=lon,
            datum=datum,
            gl_elevation_ft=self._extract_elevation(soup, full_text, 'GL Elevation', 'Ground Elevation'),
            kb_elevation_ft=self._extract_elevation(soup, full_text, 'KB Elevation', 'Kelly Bushing'),
            df_elevation_ft=self._extract_elevation(soup, full_text, 'DF Elevation', 'Derrick Floor'),
            sing_mult_compl=self._extract_field(soup, full_text, 'Sing/Mult Compl', alt_labels=['Single/Multiple Completion']),
            potash_waiver=potash_waiver,
            formation=self._extract_field(soup, full_text, 'Formation'),
            proposed_formation=self._extract_field(soup, full_text, 'Proposed Formation'),
            proposed_depth_ft=self._extract_depth(soup, full_text, 'Proposed Depth'),
            measured_vertical_depth_ft=self._extract_depth(soup, full_text, 'Measured Vertical Depth', alt_labels=['MVD']),
            true_vertical_depth_ft=self._extract_depth(soup, full_text, 'True Vertical Depth', alt_labels=['TVD']),
            plugback_measured_ft=self._extract_depth(soup, full_text, 'Plugback Measured', alt_labels=['Plugback']),
            # Legacy compatibility fields
            elevation_ft=self._extract_elevation(soup, full_text, 'GL Elevation', 'Elevation'),
            tvd_ft=self._extract_depth(soup, full_text, 'True Vertical Depth', alt_labels=['TVD']),
            event_dates=event_dates,
            spud_date=event_dates.spud_date if event_dates else self._extract_date(soup, full_text, 'Spud Date'),
            completion_date=event_dates.completion_date if event_dates else self._extract_date(soup, full_text, 'Completion', alt_labels=['Completion Date']),
            casing_records=casing_records,
            completions=completions,
            formation_tops=formation_tops,
            raw_html=html if include_raw_html else None,
        )

        return data

    def _extract_well_name_and_number(self, soup: BeautifulSoup, full_text: str) -> tuple:
        """
        Extract well name and number from header.
        Example: "30-015-28841 GOVERNMENT NBFD #001" -> ("GOVERNMENT NBFD", "001")
        
        The well name is typically in a span with id ending in 'lblApi'
        """
        well_name = ""
        well_number = None

        # Try to find the header span with well name (usually has lblApi in the id)
        header_span = soup.find('span', id=re.compile(r'lblApi$'))
        if header_span:
            header_text = header_span.get_text(strip=True)
            # Format: "30-015-28841 GOVERNMENT NBFD #001"
            # Remove the API number from the start
            # API format: xx-xxx-xxxxx
            header_text = re.sub(r'^\d{2}-\d{3}-\d{5}\s+', '', header_text)
            
            if header_text:
                # Try to extract well number from #xxx pattern
                match = re.search(r'#(\d+[A-Za-z]*)$', header_text)
                if match:
                    well_number = match.group(1)
                    # Remove the # and number from the name
                    well_name = re.sub(r'\s*#\d+[A-Za-z]*$', '', header_text).strip()
                else:
                    well_name = header_text.strip()
        
        # Fallback to old method if nothing found
        if not well_name:
            well_name = self._extract_field(soup, full_text, 'Well Name')
            if well_name:
                match = re.search(r'#(\d+[A-Za-z]*)$', well_name)
                if match:
                    well_number = match.group(1)
                    well_name = re.sub(r'\s*#\d+[A-Za-z]*$', '', well_name).strip()

        return well_name, well_number

    def _extract_coordinates(self, full_text: str) -> tuple:
        """
        Extract latitude, longitude, and datum from coordinates.

        Looks for patterns like:
        - "32.7574387,-104.0298615 NAD83"
        - "Lat: 32.7574387, Lon: -104.0298615"
        """
        lat = None
        lon = None
        datum = ""

        # Try to find coordinate pair with datum: "lat,lon NAD83"
        pattern = r'([+-]?\d+\.\d+)\s*,\s*([+-]?\d+\.\d+)\s*(NAD\d+|WGS\d+)?'
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            try:
                lat = float(match.group(1))
                lon = float(match.group(2))
                if match.group(3):
                    datum = match.group(3).upper()
            except (ValueError, TypeError):
                pass

        # If not found, try labeled coordinates
        if lat is None:
            lat_match = re.search(r'(?:Lat(?:itude)?|Y)\s*:?\s*([+-]?\d+\.\d+)', full_text, re.IGNORECASE)
            if lat_match:
                try:
                    lat = float(lat_match.group(1))
                except (ValueError, TypeError):
                    pass

        if lon is None:
            lon_match = re.search(r'(?:Lon(?:gitude)?|X)\s*:?\s*([+-]?\d+\.\d+)', full_text, re.IGNORECASE)
            if lon_match:
                try:
                    lon = float(lon_match.group(1))
                except (ValueError, TypeError):
                    pass

        return lat, lon, datum

    def _extract_boolean(self, full_text: str, *labels: str) -> Optional[bool]:
        """Extract boolean value from text (Yes/No, True/False, Y/N)."""
        for label in labels:
            pattern = rf'{re.escape(label)}\s*:?\s*(Yes|No|True|False|Y|N)\b'
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                value = match.group(1).lower()
                return value in ('yes', 'true', 'y')
        return None

    def _extract_event_dates(self, soup: BeautifulSoup, full_text: str) -> EventDates:
        """Extract all event dates from the well record."""
        return EventDates(
            initial_apd_approval=self._extract_date(soup, full_text, 'Initial APD Approval', alt_labels=['Initial APD']),
            most_recent_apd_approval=self._extract_date(soup, full_text, 'Most Recent APD Approval', alt_labels=['Most Recent APD', 'Recent APD']),
            current_apd_expiration=self._extract_date(soup, full_text, 'Current APD Expiration', alt_labels=['APD Expiration']),
            spud_date=self._extract_date(soup, full_text, 'Spud Date', alt_labels=['Spud']),
            completion_date=self._extract_date(soup, full_text, 'Completion Date', alt_labels=['Completion']),
            first_production_date=self._extract_date(soup, full_text, 'First Production', alt_labels=['First Prod']),
            last_inspection=self._extract_date(soup, full_text, 'Last Inspection', alt_labels=['Inspection']),
            last_mit_bht=self._extract_date(soup, full_text, 'Last MIT/BHT', alt_labels=['Last MIT', 'MIT/BHT', 'BHT']),
            plugging_date=self._extract_date(soup, full_text, 'Plugging Date', alt_labels=['Plugged']),
            ta_date=self._extract_date(soup, full_text, 'TA Date', alt_labels=['Temporarily Abandoned']),
        )

    def _extract_casing_records(self, soup: BeautifulSoup) -> List[CasingRecord]:
        """
        Extract casing records from the casing/strings table.

        The table has columns like:
        String/Hole Type | Taper | Date Set | Diameter | Top | Bottom (Depth) | Grade | Length | Weight |
        Bot of Cem | Top of Cem | Meth | Class of Cement | Sacks | Pressure Test (Y/N)
        """
        records = []

        # First, try to find the casing section by ID or legend
        casing_section = soup.find('div', id=re.compile(r'casing', re.IGNORECASE))
        if not casing_section:
            # Try to find by legend text
            casing_legend = soup.find('legend', string=re.compile(r'^\s*Casing\s*$', re.IGNORECASE))
            if casing_legend:
                casing_section = casing_legend.find_parent('fieldset')
        
        # If we found the casing section, look for tables within it
        if casing_section:
            tables = casing_section.find_all('table')
            for table in tables:
                # Check if this looks like the casing table by examining headers
                thead = table.find('thead')
                if thead:
                    headers = thead.find_all('th')
                    header_text = ' '.join(h.get_text(strip=True).lower() for h in headers)

                    # Look for key casing table columns
                    # NM OCD casing table has: String/Hole Type, Diameter, Bottom, Cement, Sacks
                    if 'string' in header_text or 'hole' in header_text:
                        if 'diameter' in header_text and 'bottom' in header_text:
                            parsed_records = self._parse_casing_table(table)
                            if parsed_records:
                                records.extend(parsed_records)
                                break
        
        # Fallback: search all tables if we didn't find anything in the casing section
        if not records:
            tables = soup.find_all('table')
            for table in tables:
                # Check if this looks like the casing table by examining headers
                thead = table.find('thead')
                if thead:
                    headers = thead.find_all('th')
                    header_text = ' '.join(h.get_text(strip=True).lower() for h in headers)

                    # Look for key casing table columns with stricter criteria
                    if ('string' in header_text or 'hole' in header_text) and 'diameter' in header_text and 'cement' in header_text:
                        parsed_records = self._parse_casing_table(table)
                        if parsed_records:
                            records.extend(parsed_records)
                            break

        # Last resort: try to find casing data in text patterns
        if not records:
            records = self._extract_casing_from_text(soup)

        return records

    def _parse_casing_table(self, table: Tag) -> List[CasingRecord]:
        """Parse a casing table and extract records."""
        records = []

        # Get all header rows (NM OCD has multi-row headers)
        # Find the last header row which has the actual column names
        thead = table.find('thead')
        if not thead:
            return records
        
        header_rows = thead.find_all('tr')
        if not header_rows:
            return records
        
        # Use the last header row for column mapping (it has the actual field names)
        last_header_row = header_rows[-1]
        headers = []
        for th in last_header_row.find_all(['th', 'td']):
            headers.append(th.get_text(strip=True).lower())

        # Column index mapping (handle various header names)
        col_map = {}
        for i, h in enumerate(headers):
            h_lower = h.lower()
            if 'string' in h_lower or 'hole' in h_lower and 'type' in h_lower:
                col_map['string_type'] = i
            elif h_lower == 'taper':
                col_map['taper'] = i
            elif 'date' in h_lower and 'set' in h_lower:
                col_map['date_set'] = i
            elif h_lower == 'diameter':
                col_map['diameter'] = i
            elif h_lower == 'top':
                col_map['top'] = i
            elif 'bottom' in h_lower and 'depth' in h_lower:
                col_map['bottom'] = i
            elif 'bottom' in h_lower and 'cem' not in h_lower:
                col_map['bottom'] = i
            elif h_lower == 'grade':
                col_map['grade'] = i
            elif h_lower == 'length':
                col_map['length'] = i
            elif h_lower == 'weight':
                col_map['weight'] = i
            elif 'bot' in h_lower and 'cem' in h_lower:
                col_map['cement_bottom'] = i
            elif 'top' in h_lower and 'cem' in h_lower:
                col_map['cement_top'] = i
            elif h_lower == 'meth':
                col_map['cement_method'] = i
            elif 'class' in h_lower and 'cement' in h_lower:
                col_map['cement_class'] = i
            elif h_lower == 'sacks':
                col_map['sacks'] = i
            elif 'pressure' in h_lower and 'test' in h_lower:
                col_map['pressure_test'] = i

        # Parse data rows from tbody
        tbody = table.find('tbody')
        if not tbody:
            return records
        
        rows = tbody.find_all('tr')
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 3:
                continue

            def get_cell(col_name: str) -> str:
                idx = col_map.get(col_name)
                if idx is not None and idx < len(cells):
                    return cells[idx].get_text(strip=True)
                return ""

            def get_float(col_name: str) -> Optional[float]:
                val = get_cell(col_name)
                if val:
                    try:
                        # Handle zero values
                        num = float(re.sub(r'[^\d.\-]', '', val))
                        return num if num != 0 else 0.0
                    except (ValueError, TypeError):
                        pass
                return None

            def get_int(col_name: str) -> Optional[int]:
                val = get_float(col_name)
                return int(val) if val is not None else None

            string_type = get_cell('string_type')
            if not string_type:
                continue
            
            # Skip "Hole" entries - we only want actual casing/tubing/equipment
            # Keep entries like: Surface Casing, Intermediate Casing, Production Casing, Tubing, Packer
            if string_type.startswith('Hole') and string_type.split()[0] == 'Hole':
                continue

            record = CasingRecord(
                string_type=string_type,
                taper=get_int('taper'),
                date_set=get_cell('date_set') or None,
                diameter_in=get_float('diameter'),
                top_ft=get_float('top'),
                bottom_ft=get_float('bottom'),
                grade=get_cell('grade') or None,
                length_ft=get_float('length'),
                weight_ppf=get_float('weight'),
                cement_bottom_ft=get_float('cement_bottom'),
                cement_top_ft=get_float('cement_top'),
                cement_method=get_cell('cement_method') or None,
                cement_class=get_cell('cement_class') or None,
                cement_sacks=get_int('sacks'),
                pressure_test=get_cell('pressure_test').lower() in ('y', 'yes', 'true') if get_cell('pressure_test') else None,
            )
            records.append(record)

        return records

    def _extract_casing_from_text(self, soup: BeautifulSoup) -> List[CasingRecord]:
        """
        Fallback: Extract casing data from text patterns when table parsing fails.
        """
        records = []
        full_text = soup.get_text()

        # Look for patterns like "Surface Casing: 20.000" diameter..."
        casing_patterns = [
            (r'Surface\s+Casing[:\s]+(\d+\.?\d*)"?\s*(?:diameter)?', 'Surface Casing'),
            (r'Intermediate\s*(?:1|One)?\s*Casing[:\s]+(\d+\.?\d*)"?\s*(?:diameter)?', 'Intermediate 1 Casing'),
            (r'Intermediate\s*(?:2|Two)?\s*Casing[:\s]+(\d+\.?\d*)"?\s*(?:diameter)?', 'Intermediate 2 Casing'),
            (r'Production\s+Casing[:\s]+(\d+\.?\d*)"?\s*(?:diameter)?', 'Production Casing'),
        ]

        for pattern, string_type in casing_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                try:
                    diameter = float(match.group(1))
                    records.append(CasingRecord(
                        string_type=string_type,
                        diameter_in=diameter,
                    ))
                except (ValueError, TypeError):
                    pass

        return records

    def _extract_completions(self, soup: BeautifulSoup, full_text: str) -> List[CompletionRecord]:
        """
        Extract well completion records including perforations.
        
        Completions are in a specific section with element IDs like:
        - lblWellInformationPool (contains [73280])
        - lblWellInformationPoolName (contains "BURTON FLAT; MORROW (PRO GAS)")
        """
        completions = []

        # First, try to find completions in the Well Completions section by looking for specific span IDs
        # Look for spans with IDs ending in 'lblWellInformationPool' or 'lblWellInformationPoolName'
        pool_id_spans = soup.find_all('span', id=re.compile(r'lblWellInformationPool$'))
        pool_name_spans = soup.find_all('span', id=re.compile(r'lblWellInformationPoolName$'))
        
        # Match up pool IDs with pool names
        completion_data = []
        for i, pool_id_span in enumerate(pool_id_spans):
            pool_text = pool_id_span.get_text(strip=True)
            # Extract ID from brackets: "[73280]" -> "73280"
            match = re.search(r'\[(\d+)\]', pool_text)
            if match:
                comp_id = match.group(1)
                # Get corresponding pool name
                comp_name = ""
                if i < len(pool_name_spans):
                    comp_name = pool_name_spans[i].get_text(strip=True)
                
                # Only add if we have both ID and name
                if comp_id and comp_name:
                    completion_data.append((comp_id, comp_name))
        
        # Create completion records from the structured data
        for comp_id, comp_name in completion_data:
            completion = CompletionRecord(
                completion_id=comp_id,
                completion_name=comp_name,
            )
            completions.append(completion)
        
        # If no completions found via structured approach, fall back to pattern matching
        # but be more selective about what we match
        if not completions:
            # Look for patterns only in the Well Completions section
            well_completions_section = soup.find('fieldset', id=re.compile(r'well_completions'))
            if not well_completions_section:
                # Try legend-based search
                well_completions_legend = soup.find('legend', string=re.compile(r'Well Completions', re.IGNORECASE))
                if well_completions_legend:
                    well_completions_section = well_completions_legend.find_parent('fieldset')
            
            if well_completions_section:
                section_text = well_completions_section.get_text()
                # Pattern: [84872] SAND TANK; MORROW (GAS)
                completion_pattern = r'\[(\d+)\]\s*([^\n\[]+)'
                completion_matches = re.finditer(completion_pattern, section_text)

                seen_ids = set()
                for match in completion_matches:
                    comp_id = match.group(1)
                    if comp_id in seen_ids:
                        continue
                    seen_ids.add(comp_id)

                    comp_name = match.group(2).strip() if match.group(2) else None

                    # Skip if it's a very short match (likely a false positive)
                    if not comp_name or len(comp_name) < 3:
                        continue

                    # Only process if this looks like a completion (has well/formation info)
                    # Check for common formation names or fluid type indicators
                    completion_keywords = [
                        'OIL', 'GAS', 'MORROW', 'WOLFCAMP', 'BONE', 'DELAWARE', 'PERMIAN',
                        'STRAWN', 'YESO', 'ABO', 'BRUSHY', 'CANYON', 'CISCO', 'DEVONIAN',
                        'ELLENBURGER', 'FUSSELMAN', 'GLORIETA', 'GRANITE', 'LEONARD',
                        'MONTOYA', 'QUEEN', 'SAN ANDRES', 'SIMPSON', 'SPRABERRY',
                        'TANSILL', 'TUBB', 'WICHITA', 'WOODFORD', 'FLAT', 'TANK'
                    ]
                    if comp_name and any(keyword in comp_name.upper() for keyword in completion_keywords):
                        completion = CompletionRecord(
                            completion_id=comp_id,
                            completion_name=comp_name,
                        )
                        completions.append(completion)

        # If we found completions, try to extract details for each one
        # Note: Since there are multiple completions, we need to be careful not to
        # extract the same field for all of them. For now, we'll extract common fields.
        if completions:
            # Extract shared well-level fields (these will be the same for all completions)
            status = self._extract_field(soup, full_text, 'Status')
            last_produced = self._extract_date(soup, full_text, 'Last Produced')
            bottomhole_location = self._extract_field(soup, full_text, 'Bottomhole Location', alt_labels=['Bottom Hole Location', 'BH Location'])
            production_method = self._extract_field(soup, full_text, 'Production Method', alt_labels=['Prod Method'])
            
            # Well test data (shared)
            flowing_tubing_pressure_psi = self._extract_numeric(full_text, 'Flowing Tubing Pressure', 'FTP')
            choke_size_in = self._extract_numeric(full_text, 'Choke Size')
            gas_volume_mcf = self._extract_numeric(full_text, 'Gas Volume')
            oil_volume_bbls = self._extract_numeric(full_text, 'Oil Volume')
            water_volume_bbls = self._extract_numeric(full_text, 'Water Volume')

            # Completion dates (shared)
            initial_effective_date = self._extract_date(soup, full_text, 'Initial Effective', alt_labels=['Initial Approval'])
            most_recent_approval = self._extract_date(soup, full_text, 'Most Recent Approval')
            ready_to_produce_date = self._extract_date(soup, full_text, 'Ready to Produce')
            c104_approval_date = self._extract_date(soup, full_text, 'C-104 Approval', alt_labels=['C104'])

            # Extract perforations (shared for now, would need more sophisticated parsing for per-completion perfs)
            perforations = self._extract_perforations(soup, full_text)
            
            # Apply to all completions (since we can't easily separate per-completion data)
            for comp in completions:
                comp.status = status
                comp.last_produced = last_produced
                comp.bottomhole_location = bottomhole_location
                comp.production_method = production_method
                comp.flowing_tubing_pressure_psi = flowing_tubing_pressure_psi
                comp.choke_size_in = choke_size_in
                comp.gas_volume_mcf = gas_volume_mcf
                comp.oil_volume_bbls = oil_volume_bbls
                comp.water_volume_bbls = water_volume_bbls
                comp.initial_effective_date = initial_effective_date
                comp.most_recent_approval = most_recent_approval
                comp.ready_to_produce_date = ready_to_produce_date
                comp.c104_approval_date = c104_approval_date
                comp.perforations = perforations

        return completions

    def _extract_formation_tops(self, soup: BeautifulSoup) -> List['FormationTop']:
        """
        Extract formation tops from the formation_tops section.

        The HTML contains a <div id="formation_tops"> with a table whose columns are:
        Formation | Top | Producing | Method Obtained
        """
        tops = []

        # Find the formation tops section
        section = soup.find('div', id='formation_tops')
        if not section:
            section = soup.find('fieldset', id=re.compile(r'formation.?tops', re.IGNORECASE))
        if not section:
            legend = soup.find('legend', string=re.compile(r'Formation\s+Tops', re.IGNORECASE))
            if legend:
                section = legend.find_parent('fieldset') or legend.find_parent('div')
        if not section:
            return tops

        table = section.find('table')
        if not table:
            return tops

        # Map column headers
        headers = []
        header_row = table.find('tr')
        if header_row:
            for th in header_row.find_all(['th', 'td']):
                headers.append(th.get_text(strip=True).lower())

        col_map = {}
        for i, h in enumerate(headers):
            if 'formation' in h:
                col_map['formation'] = i
            elif h == 'top':
                col_map['top'] = i
            elif 'producing' in h:
                col_map['producing'] = i
            elif 'method' in h:
                col_map['method'] = i

        # Parse data rows (skip header row)
        rows = table.find_all('tr')[1:]
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 2:
                continue

            def get_cell(col_name: str) -> str:
                idx = col_map.get(col_name)
                if idx is not None and idx < len(cells):
                    return cells[idx].get_text(strip=True)
                return ""

            formation_name = get_cell('formation')
            if not formation_name:
                continue

            top_val = get_cell('top')
            top_ft = None
            if top_val:
                try:
                    top_ft = float(re.sub(r'[^\d.\-]', '', top_val))
                except (ValueError, TypeError):
                    pass

            producing_val = get_cell('producing')
            producing = None
            if producing_val:
                producing = producing_val.lower() in ('yes', 'y', 'true')

            method_obtained = get_cell('method') or None

            tops.append(FormationTop(
                formation_name=formation_name,
                top_ft=top_ft,
                producing=producing,
                method_obtained=method_obtained,
            ))

        return tops

    def _extract_perforations(self, soup: BeautifulSoup, full_text: str) -> List[PerforationInterval]:
        """
        Extract perforation intervals from the well completions section.

        Looks for patterns like:
        - Top MD: 11120, Bottom MD: 11163
        - Perforations: 11120-11163
        - Table with Top MD, Bottom MD, Top VD, Bottom VD columns
        """
        perforations = []

        # Try to find perforation table
        tables = soup.find_all('table')
        for table in tables:
            headers = table.find_all('th')
            header_text = ' '.join(h.get_text(strip=True).lower() for h in headers)

            if 'top' in header_text and ('md' in header_text or 'perf' in header_text):
                perfs = self._parse_perforation_table(table)
                if perfs:
                    perforations.extend(perfs)
                    break

        # Fallback: extract from text patterns
        if not perforations:
            # Pattern: "11120-11163" or "11120 - 11163"
            perf_pattern = r'(?:perf|interval)[^\d]*(\d+)\s*[-–]\s*(\d+)'
            for match in re.finditer(perf_pattern, full_text, re.IGNORECASE):
                try:
                    top = float(match.group(1))
                    bottom = float(match.group(2))
                    perforations.append(PerforationInterval(
                        top_md_ft=top,
                        bottom_md_ft=bottom,
                    ))
                except (ValueError, TypeError):
                    pass

            # Pattern: "Top MD: 11120" and "Bottom MD: 11163"
            top_match = re.search(r'Top\s*MD\s*:?\s*(\d+)', full_text, re.IGNORECASE)
            bottom_match = re.search(r'Bottom\s*MD\s*:?\s*(\d+)', full_text, re.IGNORECASE)
            if top_match and bottom_match and not perforations:
                try:
                    perforations.append(PerforationInterval(
                        top_md_ft=float(top_match.group(1)),
                        bottom_md_ft=float(bottom_match.group(1)),
                    ))
                except (ValueError, TypeError):
                    pass

        return perforations

    def _parse_perforation_table(self, table: Tag) -> List[PerforationInterval]:
        """Parse a perforation table."""
        perforations = []

        # Get header row to map column indices
        headers = []
        header_row = table.find('tr')
        if header_row:
            for th in header_row.find_all(['th', 'td']):
                headers.append(th.get_text(strip=True).lower())

        col_map = {}
        for i, h in enumerate(headers):
            if 'top' in h and 'md' in h:
                col_map['top_md'] = i
            elif 'bottom' in h and 'md' in h:
                col_map['bottom_md'] = i
            elif 'top' in h and 'vd' in h:
                col_map['top_vd'] = i
            elif 'bottom' in h and 'vd' in h:
                col_map['bottom_vd'] = i

        # Parse data rows
        rows = table.find_all('tr')[1:]
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 2:
                continue

            def get_float(col_name: str) -> Optional[float]:
                idx = col_map.get(col_name)
                if idx is not None and idx < len(cells):
                    val = cells[idx].get_text(strip=True)
                    if val:
                        try:
                            return float(re.sub(r'[^\d.\-]', '', val))
                        except (ValueError, TypeError):
                            pass
                return None

            top_md = get_float('top_md')
            bottom_md = get_float('bottom_md')

            if top_md is not None or bottom_md is not None:
                perforations.append(PerforationInterval(
                    top_md_ft=top_md,
                    bottom_md_ft=bottom_md,
                    top_vd_ft=get_float('top_vd'),
                    bottom_vd_ft=get_float('bottom_vd'),
                ))

        return perforations

    def _extract_numeric(self, full_text: str, *labels: str) -> Optional[float]:
        """Extract numeric value from labeled field."""
        for label in labels:
            pattern = rf'{re.escape(label)}\s*:?\s*([\d,]+\.?\d*)\s*(?:psi|mcf|bbls|inches|in)?'
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                try:
                    return float(match.group(1).replace(',', ''))
                except (ValueError, TypeError):
                    pass
        return None

    def _extract_field(self, soup: BeautifulSoup, full_text: str, label: str, alt_labels: list = None) -> str:
        """
        Extract a labeled field value from the HTML.

        Tries multiple strategies:
        1. Find span element by ID (most reliable for NM OCD)
        2. Find label element and get associated span
        3. Look for label: value pattern in text
        4. Search in table rows
        """
        # Try all label variations
        labels_to_try = [label]
        if alt_labels:
            labels_to_try.extend(alt_labels)

        for lbl in labels_to_try:
            # Strategy 1: Look for span with specific ID pattern
            # NM OCD uses IDs like: ctl00_ctl00__main_main_ucGeneralWellInformation_lblStatus
            # Try to find span with ID containing the label name (without spaces/special chars)
            label_id_part = re.sub(r'[^A-Za-z]', '', lbl)  # Remove spaces and special chars
            span_elem = soup.find('span', id=re.compile(rf'lbl{label_id_part}$', re.IGNORECASE))
            if span_elem:
                value = span_elem.get_text(strip=True)
                if value:
                    # Make sure we didn't just get a section header
                    # Section headers are often in h3/h4 tags
                    parent_tag = span_elem.find_parent()
                    if parent_tag and parent_tag.name not in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'legend']:
                        return value
            
            # Strategy 2: Find label element and get associated span
            # Labels have 'for' attribute pointing to the span ID, or label is followed by span
            label_elem = soup.find('label', string=re.compile(rf'^{re.escape(lbl)}\s*:?$', re.IGNORECASE))
            if label_elem:
                # Check if label has 'for' attribute
                label_for = label_elem.get('for')
                if label_for:
                    span_elem = soup.find('span', id=label_for)
                    if span_elem:
                        value = span_elem.get_text(strip=True)
                        if value:
                            return value
                
                # Otherwise, try to find span in same parent or next sibling
                parent = label_elem.find_parent()
                if parent:
                    span_elem = parent.find('span')
                    if span_elem:
                        value = span_elem.get_text(strip=True)
                        if value:
                            return value
                    
                    # Try next sibling
                    next_elem = label_elem.find_next_sibling()
                    if next_elem and next_elem.name == 'span':
                        value = next_elem.get_text(strip=True)
                        if value:
                            return value

            # Strategy 3: Simple pattern matching in text (least reliable, last resort)
            # Pattern: "Label: Value" or "Label Value" on same line
            pattern = rf'{re.escape(lbl)}\s*:?\s*([^\n\r]+)'
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                # Clean up the value (remove extra whitespace)
                value = re.sub(r'\s+', ' ', value)
                # Don't return if it looks like it caught another label or section header
                if value and not re.match(r'^[A-Z][a-z]+\s*:', value):
                    # Also skip if it looks like a section title (all caps or ends with section markers)
                    if not (value.isupper() and len(value.split()) <= 3):
                        # Skip common false positives
                        skip_values = ['and/or Notes', 'Well List', 'Quick Links', 'Event Dates', 
                                       'Formation Tops', 'Depths', 'Comments', 'Casing', 'Perforations',
                                       'Well Completions', 'History']
                        if value not in skip_values:
                            return value

        return ""

    def _extract_operator_name(self, soup: BeautifulSoup, full_text: str) -> str:
        """
        Extract operator name, removing the operator number in brackets.
        Example: "[372165] Permian Resources Operating, LLC" -> "Permian Resources Operating, LLC"
        
        The operator HTML structure is:
        <span id="...lblOperator">[<a href="...">372165</a>] Permian Resources Operating, LLC</span>
        """
        # First try to find the operator span by ID
        operator_span = soup.find('span', id=re.compile(r'lblOperator$'))
        if operator_span:
            # Get the text content, which will include the operator number and name
            operator_text = operator_span.get_text(strip=True)
            # Remove the operator number in brackets: "[372165] Name" -> "Name"
            operator_name = re.sub(r'^\[\d+\]\s*', '', operator_text).strip()
            if operator_name:
                return operator_name
        
        # Fallback to old method
        operator_full = self._extract_field(soup, full_text, 'Operator')
        if operator_full:
            # Remove bracket notation like [7377] from start or end
            operator_name = re.sub(r'\s*\[\d+\]\s*', ' ', operator_full).strip()
            return operator_name
        return ""

    def _extract_operator_number(self, soup: BeautifulSoup, full_text: str) -> str:
        """
        Extract operator number from bracket notation.
        Example: "[372165] Permian Resources Operating, LLC" -> "372165"
        
        The operator number is typically in a link tag or bracket notation.
        """
        # First try to find the operator span by ID
        operator_span = soup.find('span', id=re.compile(r'lblOperator$'))
        if operator_span:
            # Look for a link tag with the operator number
            link = operator_span.find('a')
            if link:
                operator_num = link.get_text(strip=True)
                if operator_num and operator_num.isdigit():
                    return operator_num
            
            # Otherwise extract from bracket notation in text
            operator_text = operator_span.get_text(strip=True)
            match = re.search(r'\[(\d+)\]', operator_text)
            if match:
                return match.group(1)
        
        # Fallback to old method
        operator_full = self._extract_field(soup, full_text, 'Operator')
        if operator_full:
            match = re.search(r'\[(\d+)\]', operator_full)
            if match:
                return match.group(1)
        return ""

    def _extract_depth(self, soup: BeautifulSoup, full_text: str, label: str, alt_labels: list = None) -> Optional[int]:
        """Extract depth value in feet."""
        value = self._extract_field(soup, full_text, label, alt_labels)
        if value:
            # Remove everything except digits and decimal point
            digits = re.sub(r'[^0-9.]', '', value)
            try:
                return int(float(digits))
            except (ValueError, TypeError):
                logger.debug(f"Could not parse depth from: {value}")
                return None
        return None

    def _extract_elevation(self, soup: BeautifulSoup, full_text: str, *labels: str) -> Optional[float]:
        """Extract elevation value in feet."""
        for label in labels:
            value = self._extract_field(soup, full_text, label)
            if value:
                # Remove everything except digits, decimal point, and minus sign
                digits = re.sub(r'[^0-9.\-]', '', value)
                try:
                    return float(digits)
                except (ValueError, TypeError):
                    logger.debug(f"Could not parse elevation from: {value}")
        return None

    def _extract_date(self, soup: BeautifulSoup, full_text: str, label: str, alt_labels: list = None) -> Optional[str]:
        """
        Extract date string.
        Returns the date as a string in whatever format it appears.
        Validation/normalization should happen in the model layer.
        """
        value = self._extract_field(soup, full_text, label, alt_labels)
        if value:
            # Basic sanity check - should contain digits and slashes or dashes
            if re.search(r'\d+[/-]\d+[/-]\d+', value):
                # Return just the date portion
                date_match = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', value)
                if date_match:
                    return date_match.group(1)
                return value
            # Also accept formats like "Jan 15, 2024"
            if re.search(r'[A-Za-z]{3,}\s+\d+,?\s+\d{4}', value):
                return value
        return None

    def close(self):
        """Close HTTP session."""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# Convenience function
def fetch_nm_well(api: str, include_raw_html: bool = False) -> NMWellData:
    """
    Fetch NM well data for given API number.

    Args:
        api: API number in any format (will be normalized to NM format)
        include_raw_html: If True, includes raw HTML in response for debugging

    Returns:
        NMWellData with extracted fields

    Example:
        >>> well = fetch_nm_well("30-015-28692")
        >>> print(well.well_name)
        >>> print(well.operator_name)
        >>> print(f"Casing records: {len(well.casing_records)}")
        >>> for casing in well.casing_records:
        ...     print(f"  {casing.string_type}: {casing.diameter_in}\" @ {casing.bottom_ft} ft")
    """
    with NMWellScraper() as scraper:
        return scraper.fetch_well(api, include_raw_html)

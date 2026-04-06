"""
Scraper for RRC Wellbore Query (webapps2.rrc.texas.gov/EWA/wellboreQueryAction.do).

Extracts: district, county, on/off schedule, API depth, field name, operator name.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

WELLBORE_QUERY_URL = "https://webapps2.rrc.texas.gov/EWA/wellboreQueryAction.do"


def scrape_wellbore_data(api14: str) -> Dict[str, Any]:
    """
    Query RRC Wellbore Query for structured well metadata.

    Args:
        api14: 8, 10, or 14-digit API number (non-digit chars stripped).

    Returns:
        Dict with keys: district, county, schedule_status, api_depth,
        field_name, operator_name. Empty dict on failure.
    """
    api = re.sub(r"\D+", "", api14)
    if len(api) < 8:
        logger.warning(f"[WellboreScraper] API too short: {api}")
        return {}

    api8 = api[-8:]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(WELLBORE_QUERY_URL, timeout=30000)
            page.wait_for_load_state("networkidle")

            # Fill API number field and submit
            api_input = page.query_selector('input[name="searchArgs.apiNoWildHndlr.inputValue"]')
            if not api_input:
                # Try alternate field name
                api_input = page.query_selector('input[name="searchArgs.apiNoHndlr.inputValue"]')
            if not api_input:
                logger.warning("[WellboreScraper] Could not find API input field")
                return {}

            api_input.fill(api8)

            # Click search button
            search_btn = page.query_selector('input[type="button"][value="Search"]')
            if search_btn:
                search_btn.click()
            else:
                page.keyboard.press("Enter")

            page.wait_for_load_state("networkidle")

            # Parse results table
            result = _parse_wellbore_results(page)
            if result:
                logger.info(f"[WellboreScraper] Extracted data for {api8}: {list(result.keys())}")
            else:
                logger.info(f"[WellboreScraper] No data found for {api8}")
            return result

        except Exception as e:
            logger.exception(f"[WellboreScraper] Failed for {api14}: {e}")
            return {}
        finally:
            context.close()
            browser.close()


def _parse_wellbore_results(page) -> Dict[str, Any]:
    """Parse the wellbore query results page for structured data."""
    result: Dict[str, Any] = {}

    try:
        # Look for data in tables on the page
        for table in page.query_selector_all("table"):
            rows = table.query_selector_all("tr")
            for row in rows:
                cells = row.query_selector_all("td, th")
                for i in range(0, len(cells) - 1):
                    label = cells[i].inner_text().strip().lower().rstrip(":")
                    value = cells[i + 1].inner_text().strip() if i + 1 < len(cells) else ""
                    if not value or value.lower() in ("n/a", "", "none"):
                        continue

                    if "district" in label and "district" not in result:
                        result["district"] = value
                    elif "county" in label and "county" not in result:
                        result["county"] = value
                    elif "operator" in label and "operator_name" not in result:
                        result["operator_name"] = value
                    elif "field" in label and "name" in label and "field_name" not in result:
                        result["field_name"] = value
                    elif "field" in label and "field_name" not in result:
                        result["field_name"] = value
                    elif "depth" in label and "api_depth" not in result:
                        result["api_depth"] = value
                    elif ("schedule" in label or "status" in label) and "schedule_status" not in result:
                        result["schedule_status"] = value
    except Exception as e:
        logger.debug(f"[WellboreScraper] Parse error: {e}")

    return result

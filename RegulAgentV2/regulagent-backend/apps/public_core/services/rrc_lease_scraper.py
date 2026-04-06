"""
Scraper for RRC Lease Detail Hub.

Extracts: current operator, field, lease name, drilling permit history.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# The Oil & Gas Query tool provides lease-level data
LEASE_QUERY_URL = "https://webapps.rrc.texas.gov/OGA/publicSearchAction.do"


def scrape_lease_data(api14: str) -> Dict[str, Any]:
    """
    Query RRC Lease Detail for structured well/lease metadata.

    Args:
        api14: 8, 10, or 14-digit API number (non-digit chars stripped).

    Returns:
        Dict with keys: operator_name, field_name, lease_name, permits.
        Empty dict on failure.
    """
    api = re.sub(r"\D+", "", api14)
    if len(api) < 8:
        logger.warning(f"[LeaseScraper] API too short: {api}")
        return {}

    api8 = api[-8:]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(LEASE_QUERY_URL, timeout=30000)
            page.wait_for_load_state("networkidle")

            # Fill API number and submit
            api_input = page.query_selector('input[name="searchArgs.apiNoHndlr.inputValue"]')
            if not api_input:
                api_input = page.query_selector('input[name="searchArgs.apiNoWildHndlr.inputValue"]')
            if not api_input:
                logger.warning("[LeaseScraper] Could not find API input field")
                return {}

            api_input.fill(api8)

            search_btn = page.query_selector('input[type="button"][value="Search"]')
            if search_btn:
                search_btn.click()
            else:
                page.keyboard.press("Enter")

            page.wait_for_load_state("networkidle")

            result = _parse_lease_results(page)

            # If there's a detail link, navigate to it for more info
            detail_link = page.query_selector("a[href*='leaseDetail'], a[href*='LeaseDetail']")
            if not detail_link:
                # Try clicking the first result row link
                detail_link = page.query_selector("table.DataGrid tr td a")

            if detail_link:
                try:
                    detail_link.click()
                    page.wait_for_load_state("networkidle")
                    detail_data = _parse_lease_detail_page(page)
                    # Merge detail data (don't overwrite existing)
                    for k, v in detail_data.items():
                        if k not in result or not result[k]:
                            result[k] = v
                except Exception as e:
                    logger.debug(f"[LeaseScraper] Could not navigate to detail: {e}")

            if result:
                logger.info(f"[LeaseScraper] Extracted data for {api8}: {list(result.keys())}")
            else:
                logger.info(f"[LeaseScraper] No data found for {api8}")
            return result

        except Exception as e:
            logger.exception(f"[LeaseScraper] Failed for {api14}: {e}")
            return {}
        finally:
            context.close()
            browser.close()


def _parse_lease_results(page) -> Dict[str, Any]:
    """Parse the lease query search results page."""
    result: Dict[str, Any] = {}

    try:
        for table in page.query_selector_all("table"):
            rows = table.query_selector_all("tr")
            for row in rows:
                cells = row.query_selector_all("td, th")
                for i in range(0, len(cells) - 1):
                    label = cells[i].inner_text().strip().lower().rstrip(":")
                    value = cells[i + 1].inner_text().strip() if i + 1 < len(cells) else ""
                    if not value or value.lower() in ("n/a", "", "none"):
                        continue

                    if "operator" in label and "operator_name" not in result:
                        result["operator_name"] = value
                    elif "field" in label and "field_name" not in result:
                        result["field_name"] = value
                    elif "lease" in label and "name" in label and "lease_name" not in result:
                        result["lease_name"] = value
                    elif "lease" in label and "lease_name" not in result:
                        result["lease_name"] = value
    except Exception as e:
        logger.debug(f"[LeaseScraper] Parse error: {e}")

    return result


def _parse_lease_detail_page(page) -> Dict[str, Any]:
    """Parse the lease detail page for additional metadata."""
    result: Dict[str, Any] = {}
    permits: List[Dict[str, str]] = []

    try:
        for table in page.query_selector_all("table"):
            rows = table.query_selector_all("tr")
            for row in rows:
                cells = row.query_selector_all("td, th")
                for i in range(0, len(cells) - 1):
                    label = cells[i].inner_text().strip().lower().rstrip(":")
                    value = cells[i + 1].inner_text().strip() if i + 1 < len(cells) else ""
                    if not value or value.lower() in ("n/a", "", "none"):
                        continue

                    if "operator" in label and "operator_name" not in result:
                        result["operator_name"] = value
                    elif "field" in label and "field_name" not in result:
                        result["field_name"] = value
                    elif "lease" in label and "name" in label and "lease_name" not in result:
                        result["lease_name"] = value
                    elif "county" in label and "county" not in result:
                        result["county"] = value
                    elif "district" in label and "district" not in result:
                        result["district"] = value

            # Look for permit tables
            header_text = " ".join(
                c.inner_text().strip().lower()
                for c in (rows[0].query_selector_all("th, td") if rows else [])
            )
            if "permit" in header_text:
                for row in rows[1:]:
                    cells = row.query_selector_all("td")
                    if len(cells) >= 2:
                        permit_entry = {
                            "number": cells[0].inner_text().strip(),
                            "type": cells[1].inner_text().strip() if len(cells) > 1 else "",
                            "date": cells[2].inner_text().strip() if len(cells) > 2 else "",
                        }
                        if permit_entry["number"]:
                            permits.append(permit_entry)

        if permits:
            result["permits"] = permits
    except Exception as e:
        logger.debug(f"[LeaseScraper] Detail parse error: {e}")

    return result

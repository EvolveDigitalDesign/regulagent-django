"""
NM Well Import Service

Imports well data from NM OCD by scraping and creates WellRegistry entries.
Integrates NMWellScraper with WellRegistry model.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Optional
from decimal import Decimal

from django.db import transaction

from apps.public_core.models import WellRegistry
from apps.public_core.services.nm_well_scraper import NMWellScraper, NMWellData
from apps.public_core.services.api_normalization import normalize_api_14digit

logger = logging.getLogger(__name__)


def import_nm_well(
    api: str,
    workspace_id: Optional[int] = None,
    update_existing: bool = True
) -> Dict[str, Any]:
    """
    Import NM well data from OCD scraper and create/update WellRegistry entry.

    Args:
        api: NM API number in any format (will be normalized to API-14)
        workspace_id: Optional workspace ID to assign the well to
        update_existing: If True, update existing well data; if False, skip existing wells

    Returns:
        Dict with:
            - status: "created" or "updated" or "exists"
            - well: WellRegistry instance
            - scraped_data: Raw NMWellData dict
            - errors: List of errors (if any)

    Raises:
        ValueError: If API format is invalid
        requests.HTTPError: If scraping fails
    """
    errors = []

    try:
        # Step 1: Scrape NM OCD data
        logger.info(f"Scraping NM well data for API: {api}")
        with NMWellScraper() as scraper:
            nm_data = scraper.fetch_well(api, include_raw_html=False)

        # Step 2: Normalize API to 14-digit format
        # NM API-10 is like 30-015-28692, need to convert to 14-digit
        api14 = nm_data.api14

        logger.info(f"Normalized NM API {nm_data.api10} to API-14: {api14}")

        # Step 3: Check if well already exists
        existing_well = WellRegistry.objects.filter(api14=api14).first()

        if existing_well and not update_existing:
            logger.info(f"Well {api14} already exists, skipping update")
            return {
                "status": "exists",
                "well": existing_well,
                "scraped_data": nm_data.to_dict(),
                "errors": [],
            }

        # Step 4: Map NM data to WellRegistry fields
        well_data = _map_nm_data_to_well_registry(nm_data, workspace_id)

        # Step 5: Create or update WellRegistry entry
        with transaction.atomic():
            if existing_well:
                # Update existing well
                for field, value in well_data.items():
                    # Only update if new value is not None/empty and existing is empty
                    if value and not getattr(existing_well, field):
                        setattr(existing_well, field, value)
                existing_well.save()
                logger.info(f"Updated WellRegistry for API {api14}")
                status = "updated"
                well = existing_well
            else:
                # Create new well
                well = WellRegistry.objects.create(**well_data)
                logger.info(f"Created WellRegistry for API {api14}")
                status = "created"

        return {
            "status": status,
            "well": well,
            "scraped_data": nm_data.to_dict(),
            "errors": errors,
        }

    except ValueError as e:
        logger.error(f"Invalid API format for {api}: {e}")
        raise
    except Exception as e:
        logger.error(f"Failed to import NM well {api}: {e}", exc_info=True)
        errors.append(str(e))
        raise


def _map_nm_data_to_well_registry(
    nm_data: NMWellData,
    workspace_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Map NMWellData fields to WellRegistry model fields.

    Field mappings:
    - api14: api14 (already normalized)
    - state: "NM" (hardcoded for New Mexico)
    - county: Extract from surface_location or district
    - district: NM district (if available)
    - operator_name: operator_name
    - field_name: formation (NM uses formation field)
    - lease_name: Parse from well_name (if available)
    - well_number: Parse from well_name (if available)
    - lat: latitude
    - lon: longitude

    Args:
        nm_data: NMWellData from scraper
        workspace_id: Optional workspace ID

    Returns:
        Dict of WellRegistry field values
    """
    from apps.tenants.models import ClientWorkspace

    # Parse well name to extract lease and well number
    # NM well names are often like "STATE FEDERAL 1" or "FEDERAL 1-30H"
    lease_name = ""
    well_number = ""
    if nm_data.well_name:
        # Simple heuristic: last token with digit is well number, rest is lease
        tokens = nm_data.well_name.strip().split()
        if tokens:
            # Check if last token looks like a well number (contains digit)
            if any(char.isdigit() for char in tokens[-1]):
                well_number = tokens[-1]
                lease_name = " ".join(tokens[:-1])
            else:
                lease_name = nm_data.well_name

    # Extract county from surface location if available
    # NM surface locations often include county: "320 FNL, 660 FWL, SEC 30 T16S R33E, LEA COUNTY"
    county = ""
    if nm_data.surface_location:
        # Look for "COUNTY" in surface location
        location_upper = nm_data.surface_location.upper()
        if "COUNTY" in location_upper:
            # Extract county name before "COUNTY"
            parts = location_upper.split("COUNTY")
            if parts:
                county_part = parts[0].strip()
                # Get last word before COUNTY
                county_tokens = county_part.split(",")
                if county_tokens:
                    county = county_tokens[-1].strip()

    # Build WellRegistry data
    well_data = {
        "api14": nm_data.api14,
        "state": "NM",
        "county": county[:64] if county else "",
        "district": "",  # NM uses districts differently than TX, leave blank for now
        "operator_name": nm_data.operator_name[:128] if nm_data.operator_name else "",
        "field_name": nm_data.formation[:128] if nm_data.formation else "",
        "lease_name": lease_name[:128] if lease_name else "",
        "well_number": well_number[:32] if well_number else "",
    }

    # Add coordinates if available
    if nm_data.latitude is not None:
        well_data["lat"] = Decimal(str(nm_data.latitude))
    if nm_data.longitude is not None:
        well_data["lon"] = Decimal(str(nm_data.longitude))

    # Add workspace if provided
    if workspace_id:
        workspace = ClientWorkspace.objects.filter(id=workspace_id).first()
        if workspace:
            well_data["workspace"] = workspace
        else:
            logger.warning(f"Workspace {workspace_id} not found, creating well without workspace")

    return well_data


def batch_import_nm_wells(
    api_list: list[str],
    workspace_id: Optional[int] = None,
    update_existing: bool = True
) -> Dict[str, Any]:
    """
    Batch import multiple NM wells.

    Args:
        api_list: List of NM API numbers
        workspace_id: Optional workspace ID to assign wells to
        update_existing: If True, update existing wells

    Returns:
        Dict with:
            - total: Total wells processed
            - created: Number of wells created
            - updated: Number of wells updated
            - exists: Number of wells skipped (already exist)
            - failed: Number of failed imports
            - results: List of individual results
            - errors: List of errors
    """
    results = []
    created = 0
    updated = 0
    exists = 0
    failed = 0
    errors = []

    for api in api_list:
        try:
            result = import_nm_well(api, workspace_id, update_existing)
            results.append(result)

            if result["status"] == "created":
                created += 1
            elif result["status"] == "updated":
                updated += 1
            elif result["status"] == "exists":
                exists += 1

        except Exception as e:
            failed += 1
            error_msg = f"Failed to import {api}: {str(e)}"
            errors.append(error_msg)
            logger.error(error_msg)
            results.append({
                "status": "failed",
                "api": api,
                "error": str(e),
            })

    return {
        "total": len(api_list),
        "created": created,
        "updated": updated,
        "exists": exists,
        "failed": failed,
        "results": results,
        "errors": errors,
    }

"""
Management command to import NM wells from OCD scraper.

Usage:
    python manage.py import_nm_well --api 30-015-28692
    python manage.py import_nm_well --api 30-015-28692 --workspace 1
    python manage.py import_nm_well --api 30-015-28692 --update
"""
from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.public_core.services.nm_well_import import import_nm_well


class Command(BaseCommand):
    help = "Import NM well data from OCD scraper and create WellRegistry entry"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--api",
            dest="api",
            required=True,
            help="NM API number (e.g., 30-015-28692 or 3001528692)"
        )
        parser.add_argument(
            "--workspace",
            dest="workspace",
            type=int,
            default=None,
            help="Client workspace ID to assign the well to (optional)"
        )
        parser.add_argument(
            "--update",
            dest="update",
            action="store_true",
            help="Update existing well if it already exists"
        )
        parser.add_argument(
            "--dry",
            dest="dry",
            action="store_true",
            help="Dry run - scrape data but don't create/update well"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        api = options.get("api")
        workspace_id = options.get("workspace")
        update_existing = options.get("update", False)
        dry_run = options.get("dry", False)

        if not api:
            self.stderr.write(
                json.dumps({"error": "API number is required"}, ensure_ascii=False)
            )
            return

        try:
            if dry_run:
                # Just scrape and show data without persisting
                from apps.public_core.services.nm_well_scraper import NMWellScraper

                self.stdout.write(f"Dry run: scraping NM well data for API {api}...")
                with NMWellScraper() as scraper:
                    nm_data = scraper.fetch_well(api, include_raw_html=False)

                result = {
                    "status": "dry_run",
                    "api": api,
                    "scraped_data": nm_data.to_dict(),
                }
                self.stdout.write(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                # Import well
                self.stdout.write(f"Importing NM well for API {api}...")
                result = import_nm_well(
                    api=api,
                    workspace_id=workspace_id,
                    update_existing=update_existing
                )

                # Convert well instance to dict for JSON output
                well = result.pop("well")
                output = {
                    "status": result["status"],
                    "well": {
                        "id": well.id,
                        "api14": well.api14,
                        "state": well.state,
                        "county": well.county,
                        "district": well.district,
                        "operator_name": well.operator_name,
                        "field_name": well.field_name,
                        "lease_name": well.lease_name,
                        "well_number": well.well_number,
                        "lat": str(well.lat) if well.lat else None,
                        "lon": str(well.lon) if well.lon else None,
                        "workspace_id": well.workspace_id,
                    },
                    "scraped_data": result["scraped_data"],
                    "errors": result["errors"],
                }

                self.stdout.write(json.dumps(output, indent=2, ensure_ascii=False))

                # Log success
                if result["status"] == "created":
                    self.stdout.write(
                        self.style.SUCCESS(f"Successfully created well {well.api14}")
                    )
                elif result["status"] == "updated":
                    self.stdout.write(
                        self.style.SUCCESS(f"Successfully updated well {well.api14}")
                    )
                elif result["status"] == "exists":
                    self.stdout.write(
                        self.style.WARNING(f"Well {well.api14} already exists")
                    )

        except ValueError as e:
            self.stderr.write(
                self.style.ERROR(f"Invalid API format: {e}")
            )
            self.stderr.write(json.dumps({"error": "invalid_api", "message": str(e)}, ensure_ascii=False))

        except Exception as e:
            self.stderr.write(
                self.style.ERROR(f"Failed to import NM well: {e}")
            )
            self.stderr.write(json.dumps({"error": "import_failed", "message": str(e)}, ensure_ascii=False))

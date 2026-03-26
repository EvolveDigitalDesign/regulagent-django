"""
Management command to re-attribute ExtractedDocuments using multi-field resolution.

Usage:
    python manage.py reattribute_documents --api14 42003356630000 --dry-run
    python manage.py reattribute_documents --lease-id 12345 --dry-run
    python manage.py reattribute_documents --all-low-confidence --dry-run
    python manage.py reattribute_documents --all-low-confidence --limit 500
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.db.models import Q

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Re-attribute ExtractedDocuments using multi-field well resolution"

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--api14",
            type=str,
            help="Re-attribute documents currently assigned to this API14",
        )
        group.add_argument(
            "--lease-id",
            type=str,
            help="Re-attribute all documents for wells on this lease",
        )
        group.add_argument(
            "--all-low-confidence",
            action="store_true",
            help="Re-attribute all documents with low attribution confidence",
        )

        parser.add_argument(
            "--force-ocr",
            action="store_true",
            help="Always run OCR on documents regardless of current confidence level",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show proposed changes without applying them",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=1000,
            help="Max documents to process (default: 1000)",
        )

    def handle(self, *args, **options):
        from apps.public_core.models import ExtractedDocument, WellRegistry
        from apps.public_core.services.neubus_extractor import _resolve_well_attribution
        from apps.public_core.services.well_registry_enrichment import build_lease_well_map

        dry_run = options["dry_run"]
        limit = options["limit"]

        # Build queryset
        if options.get("api14"):
            api14 = options["api14"]
            # Match both 14-digit and 10-digit API formats, plus any
            # documents linked to the same well or lease
            import re
            api_digits = re.sub(r"\D", "", api14)
            well = WellRegistry.objects.filter(api14=api14).first()
            lease_id = well.lease_id if well else ""
            state = "TX" if api14.startswith("42") else "NM" if api14.startswith("30") else ""

            # Build broad match: exact api14, 10-digit prefix, 5-digit core,
            # plus any docs linked to the same well or lease siblings
            q = (
                Q(api_number=api14)
                | Q(api_number=api_digits[:10])  # 10-digit format
                | Q(api_number__contains=api_digits[2:7])  # 5-digit core (county+well)
            )
            if well:
                q |= Q(well=well)
            if lease_id:
                q |= Q(well__lease_id=lease_id)
            eds = ExtractedDocument.objects.filter(q).distinct()
            self.stdout.write(f"Targeting documents for API {api14} (lease_id={lease_id})")

        elif options.get("lease_id"):
            lease_id = options["lease_id"]
            wells = WellRegistry.objects.filter(lease_id=lease_id)
            api_list = list(wells.values_list("api14", flat=True))
            eds = ExtractedDocument.objects.filter(
                Q(api_number__in=api_list)
                | Q(well__lease_id=lease_id)
            )
            state = "TX"  # default, could be smarter
            self.stdout.write(f"Targeting documents for lease {lease_id} ({len(api_list)} wells)")

        elif options.get("all_low_confidence"):
            eds = ExtractedDocument.objects.filter(
                Q(attribution_confidence="low") | Q(attribution_confidence="")
            )
            lease_id = ""
            state = ""
            self.stdout.write("Targeting all low-confidence documents")

        else:
            self.stderr.write("No target specified")
            return

        eds = eds.order_by("-created_at")[:limit]
        total = eds.count()
        self.stdout.write(f"Found {total} documents to process")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be made"))

        # Build lease-well maps as needed
        lease_maps = {}  # cache: lease_id -> well_map

        changed = 0
        unchanged = 0
        errors = 0

        for ed in eds:
            try:
                # Determine lease context for this ED
                ed_lease_id = lease_id
                ed_state = state

                if not ed_lease_id and ed.well:
                    ed_lease_id = ed.well.lease_id or ""
                if not ed_state and ed.api_number:
                    ed_state = "TX" if ed.api_number.startswith("42") else "NM" if ed.api_number.startswith("30") else ""

                # Get or build lease-well map
                lwm = {}
                if ed_lease_id:
                    if ed_lease_id not in lease_maps:
                        try:
                            lease_maps[ed_lease_id] = build_lease_well_map(ed_lease_id, ed_state)
                        except Exception:
                            lease_maps[ed_lease_id] = {}
                    lwm = lease_maps[ed_lease_id]

                # Look up Neubus document well_number if available
                neubus_wn = ""
                if ed.neubus_filename:
                    from apps.public_core.models.neubus_lease import NeubusDocument
                    ndoc = NeubusDocument.objects.filter(
                        neubus_filename=ed.neubus_filename
                    ).first()
                    if ndoc:
                        neubus_wn = ndoc.well_number or ""

                # Run resolution
                resolved_api, confidence, method = _resolve_well_attribution(
                    json_data=ed.json_data,
                    fallback_api=ed.api_number,
                    state=ed_state,
                    lease_id=ed_lease_id,
                    lease_well_map=lwm,
                    neubus_well_number=neubus_wn,
                )

                # OCR escalation: scan the document's own page for the API
                # Skip if we already have high confidence from extracted_api
                # (that means the LLM read the API from the form's own content)
                force_ocr = options.get("force_ocr", False)
                should_ocr = (
                    (confidence == "low" or (force_ocr and method != "extracted_api"))
                    and ed.neubus_filename
                    and ndoc
                )
                if should_ocr:
                    try:
                        from pathlib import Path
                        from apps.public_core.services.ocr_api_detector import detect_api_from_pdf
                        pdf_path = Path(ndoc.local_path)
                        if pdf_path.exists():
                            # Build page list: form's source page, page before it,
                            # and page 0 (PDF header). Deduplicate.
                            scan_pages = []
                            src = (ed.source_page - 1) if ed.source_page else 0
                            scan_pages.append(src)
                            if src > 0:
                                scan_pages.append(src - 1)
                            if 0 not in scan_pages:
                                scan_pages.append(0)
                            ocr_result = detect_api_from_pdf(
                                pdf_path, pages=scan_pages,
                                use_vision_fallback=True,
                            )
                            if ocr_result and ocr_result.get("api"):
                                ocr_api = ocr_result["api"]
                                if len(ocr_api) >= 8:
                                    resolved_api = ocr_api
                                    confidence = ocr_result.get("confidence", "medium")
                                    method = ocr_result["method"]
                    except Exception as ocr_err:
                        self.stderr.write(f"  OCR failed for ED {ed.id}: {ocr_err}")

                # Check if anything changed
                api_changed = resolved_api != ed.api_number
                conf_changed = confidence != (ed.attribution_confidence or "low")
                method_changed = method != (ed.attribution_method or "session_fallback")

                if api_changed or conf_changed or method_changed:
                    changed += 1
                    self.stdout.write(
                        f"  ED {ed.id} ({ed.document_type}): "
                        f"api {ed.api_number} -> {resolved_api}, "
                        f"confidence {ed.attribution_confidence} -> {confidence}, "
                        f"method {ed.attribution_method} -> {method}"
                    )

                    if not dry_run:
                        update_fields = ["attribution_confidence", "attribution_method"]
                        ed.attribution_confidence = confidence
                        ed.attribution_method = method

                        if api_changed:
                            ed.api_number = resolved_api
                            update_fields.append("api_number")

                            # Relink well FK
                            api14 = resolved_api.ljust(14, "0") if len(resolved_api) < 14 else resolved_api[:14]
                            new_well = WellRegistry.objects.filter(api14=api14).first()
                            if new_well:
                                ed.well = new_well
                                update_fields.append("well")

                        ed.save(update_fields=update_fields)
                else:
                    unchanged += 1

            except Exception as e:
                errors += 1
                self.stderr.write(f"  Error processing ED {ed.id}: {e}")
                logger.exception(f"Reattribution error for ED {ed.id}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Done. Changed: {changed}, Unchanged: {unchanged}, Errors: {errors}"
        ))
        if dry_run:
            self.stdout.write(self.style.WARNING(
                "This was a dry run. Re-run without --dry-run to apply changes."
            ))

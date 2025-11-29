"""
Django management command to build W-3 form from pnaexchange payload

Usage:
    python manage.py build_w3_from_pna --pna-json /path/to/pna.json --w3a-pdf /path/to/w3a.pdf
    
Example:
    python manage.py build_w3_from_pna \
        --pna-json tmp/midland_farms_unit_90_extracted.json \
        --w3a-pdf "tmp/W3A_Examples/MF Unit 90.pdf"

Output:
    Saves generated W-3 form to tmp/w3_output.json
"""

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.public_core.services.w3_builder import build_w3_from_pna_payload


class Command(BaseCommand):
    help = "Build W-3 form from pnaexchange payload and W-3A PDF"

    def add_arguments(self, parser):
        parser.add_argument(
            "--pna-json",
            type=str,
            required=True,
            help="Path to pnaexchange JSON file with events",
        )
        parser.add_argument(
            "--w3a-pdf",
            type=str,
            required=True,
            help="Path to W-3A PDF file",
        )
        parser.add_argument(
            "--output",
            type=str,
            default="tmp/w3_output.json",
            help="Output file path (default: tmp/w3_output.json)",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show detailed logging",
        )

    def handle(self, *args: Any, **options: Any) -> str:
        pna_json_path = Path(options["pna_json"])
        w3a_pdf_path = Path(options["w3a_pdf"])
        output_path = Path(options["output"])
        verbose = options.get("verbose", False)

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS("🧪 W-3 BUILDER - Management Command"))
        self.stdout.write("=" * 80 + "\n")

        # ============================================================
        # STEP 1: Validate inputs
        # ============================================================
        self.stdout.write("📥 STEP 1: Validating inputs...\n")

        if not pna_json_path.exists():
            raise CommandError(f"❌ PNA JSON not found: {pna_json_path}")

        if not w3a_pdf_path.exists():
            raise CommandError(f"❌ W-3A PDF not found: {w3a_pdf_path}")

        self.stdout.write(f"✅ PNA JSON: {pna_json_path}")
        self.stdout.write(f"✅ W-3A PDF: {w3a_pdf_path}\n")

        # ============================================================
        # STEP 2: Load pnaexchange data
        # ============================================================
        self.stdout.write("📋 STEP 2: Loading pnaexchange data...\n")

        try:
            with open(pna_json_path, "r") as f:
                pna_data = json.load(f)

            w3_form_data = pna_data.get("w3_form", {})
            pna_events = w3_form_data.get("events", [])

            self.stdout.write(f"✅ Loaded {len(pna_events)} pnaexchange events")
            self.stdout.write(
                f"   Well: {w3_form_data.get('well', {}).get('well_name')}"
            )
            self.stdout.write(
                f"   API: {w3_form_data.get('well', {}).get('api_number')}"
            )
            self.stdout.write(
                f"   Date range: {w3_form_data.get('summary', {}).get('date_range')}\n"
            )

        except Exception as e:
            raise CommandError(f"❌ Failed to load PNA JSON: {e}")

        # ============================================================
        # STEP 3: Build payload (simulating API request)
        # ============================================================
        self.stdout.write("🔨 STEP 3: Building W-3 builder payload...\n")

        payload = {
            "dwr_id": w3_form_data.get("subproject", {}).get("id"),
            "api_number": w3_form_data.get("well", {}).get("api_number"),
            "well_name": w3_form_data.get("well", {}).get("well_name"),
            "w3a_reference": {
                "type": "pdf",
                "w3a_file": self._create_uploaded_file(w3a_pdf_path),
            },
            "pna_events": [
                {
                    "event_id": self._map_event_type_to_id(e.get("event_type")),
                    "display_text": e.get("event_type"),
                    "event_detail": e.get("event_detail"),  # Include actual event description
                    "date": e.get("date"),
                    "start_time": e.get("start_time"),
                    "end_time": e.get("end_time"),
                    "work_assignment_id": e.get("work_assignment_id"),
                    "dwr_id": e.get("dwr_id"),
                    "input_values": e.get("input_values", {}),
                    "transformation_rules": e.get("transformation_rules", {}),
                }
                for e in pna_events
            ],
        }

        self.stdout.write(f"✅ Payload built with {len(payload['pna_events'])} events\n")
        
        # DEBUG: Show plug event details
        for event in payload['pna_events']:
            if event.get('event_id') in (3, 4, 7):  # Plug events
                self.stdout.write(f"  Event ID {event.get('event_id')}: {event.get('display_text')} - {event.get('event_detail')}")
        self.stdout.write("")

        # ============================================================
        # STEP 4: Run W3 builder (simulating API endpoint)
        # ============================================================
        self.stdout.write("🏗️ STEP 4: Running W-3 builder...\n")

        try:
            # Create a mock request object with FILES
            mock_request = type(
                "MockRequest",
                (),
                {"FILES": {"w3a_file": payload["w3a_reference"]["w3a_file"]}},
            )()

            result = build_w3_from_pna_payload(payload, request=mock_request)

        except Exception as e:
            raise CommandError(f"❌ W-3 builder failed: {e}")

        # ============================================================
        # STEP 5: Display results
        # ============================================================
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS("📊 RESULTS"))
        self.stdout.write("=" * 80 + "\n")

        if result.get("success"):
            self.stdout.write(self.style.SUCCESS("✅ SUCCESS\n"))

            w3_form = result.get("w3_form", {})

            # Header
            self.stdout.write("📋 HEADER:")
            header = w3_form.get("header", {})
            self.stdout.write(f"   API: {header.get('api_number')}")
            self.stdout.write(f"   Well: {header.get('well_name')}")
            self.stdout.write(f"   Operator: {header.get('operator')}")
            self.stdout.write(f"   County: {header.get('county')}")
            self.stdout.write(f"   RRC District: {header.get('rrc_district')}\n")

            # Plugs
            plugs = w3_form.get("plugs", [])
            self.stdout.write(f"⚙️  PLUGS ({len(plugs)} total):\n")

            for plug in plugs[:6]:  # Show first 6
                self.stdout.write(f"   Plug #{plug.get('plug_number')}:")
                self.stdout.write(
                    f"     Depths: {plug.get('depth_top_ft')} - {plug.get('depth_bottom_ft')} ft"
                )
                self.stdout.write(f"     Type: {plug.get('type')}")
                self.stdout.write(f"     Cement Class: {plug.get('cement_class')}")
                self.stdout.write(f"     Sacks: {plug.get('sacks')}")
                self.stdout.write(f"     Hole Size: {plug.get('hole_size_in')}\"")
                self.stdout.write(
                    f"     Slurry Weight: {plug.get('slurry_weight_ppg')} lbs/gal"
                )
                self.stdout.write(
                    f"     TOC (measured): {plug.get('measured_top_of_plug_ft')} ft"
                )
                calc_toc = plug.get("calculated_top_of_plug_ft")
                calc_str = f"{calc_toc:.1f} ft" if calc_toc is not None else "N/A"
                self.stdout.write(f"     TOC (calculated): {calc_str}")
                if plug.get("toc_variance_ft") is not None:
                    self.stdout.write(
                        f"     TOC Variance: {plug.get('toc_variance_ft'):+.1f} ft"
                    )
                self.stdout.write("")

            if len(plugs) > 6:
                self.stdout.write(f"   ... and {len(plugs) - 6} more plugs\n")

            # Validation
            validation = result.get("validation", {})
            warnings = validation.get("warnings", [])
            errors = validation.get("errors", [])

            if warnings:
                self.stdout.write(self.style.WARNING(f"⚠️  WARNINGS ({len(warnings)}):"))
                for w in warnings[:3]:
                    self.stdout.write(f"   - {w}")
                if len(warnings) > 3:
                    self.stdout.write(f"   ... and {len(warnings) - 3} more")
                self.stdout.write("")

            if errors:
                self.stdout.write(self.style.ERROR(f"❌ ERRORS ({len(errors)}):"))
                for e in errors:
                    self.stdout.write(f"   - {e}")
                self.stdout.write("")

            # Metadata
            metadata = result.get("metadata", {})
            self.stdout.write("📊 METADATA:")
            self.stdout.write(
                f"   Events processed: {metadata.get('events_processed')}"
            )
            self.stdout.write(f"   Plugs grouped: {metadata.get('plugs_grouped')}")
            self.stdout.write(f"   Generated: {metadata.get('generated_at')}\n")

        else:
            self.stdout.write(
                self.style.ERROR(f"❌ FAILED: {result.get('error')}\n")
            )

        # ============================================================
        # STEP 6: Save to file
        # ============================================================
        self.stdout.write("💾 STEP 5: Saving output...\n")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)

        self.stdout.write(self.style.SUCCESS(f"✅ Saved to: {output_path}"))
        self.stdout.write(f"   Size: {output_path.stat().st_size} bytes\n")

        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("✅ COMPLETE"))
        self.stdout.write("=" * 80 + "\n")

        return f"W-3 form generated successfully and saved to {output_path}"

    def _map_event_type_to_id(self, event_type: str) -> int:
        """Map pnaexchange event_type string to event_id."""
        mapping = {
            "Set Intermediate Plug": 4,
            "Set Surface Plug": 3,
            "Squeeze": 7,
            "Broke Circulation": 2,
            "Pressure Up": 9,
            "Set CIBP": 6,
            "Cut Casing": 12,
            "Tag TOC": 8,
            "Tagged TOC": 5,
            "Perforation": 1,
            "Tag CIBP": 11,
            "RRC Approval": 10,
        }

        event_lower = event_type.lower()

        # Try exact match first
        for key, val in mapping.items():
            if key == event_type:
                return val

        # Try substring match
        for key, val in mapping.items():
            if key.lower() in event_lower or event_lower in key.lower():
                return val

        # Default based on substring patterns
        if "plug" in event_lower and "intermediate" in event_lower:
            return 4
        elif "plug" in event_lower and "surface" in event_lower:
            return 3
        elif "squeeze" in event_lower:
            return 7
        elif "circulation" in event_lower:
            return 2
        elif "pressure" in event_lower:
            return 9
        elif "cibp" in event_lower or "bridge" in event_lower:
            return 6
        elif "cut" in event_lower and "casing" in event_lower:
            return 12
        elif "tag" in event_lower and ("toc" in event_lower or "cement" in event_lower):
            return 8
        elif "perf" in event_lower:
            return 1
        elif "approval" in event_lower:
            return 10

        return 1  # Default to perforation

    def _create_uploaded_file(self, pdf_path: Path) -> SimpleUploadedFile:
        """Create a SimpleUploadedFile from a PDF path."""
        with open(pdf_path, "rb") as f:
            return SimpleUploadedFile(
                name=pdf_path.name,
                content=f.read(),
                content_type="application/pdf",
            )


"""
Management command for previewing W-3 PDF output.

Usage:
    manage.py preview_w3_pdf          # Generate sample PDF
    manage.py preview_w3_pdf --grid   # Generate coordinate grid overlay
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Preview W-3 PDF generator output for visual verification"

    def add_arguments(self, parser):
        parser.add_argument(
            '--grid',
            action='store_true',
            help='Generate coordinate grid overlay instead of sample form',
        )
        parser.add_argument(
            '--output', '-o',
            type=str,
            default=None,
            help='Custom output path for the PDF',
        )

    def handle(self, *args, **options):
        from apps.public_core.services.w3_pdf_generator import (
            generate_w3_pdf,
            draw_coordinate_grid,
            W3PDFGeneratorError,
        )

        if options['grid']:
            self.stdout.write("Generating coordinate grid overlay...")
            try:
                path = draw_coordinate_grid(output_path=options.get('output'))
                self.stdout.write(self.style.SUCCESS(f"Grid PDF saved to: {path}"))
            except W3PDFGeneratorError as e:
                self.stderr.write(self.style.ERROR(f"Error: {e}"))
            return

        # Generate sample PDF with fixture data
        self.stdout.write("Generating sample W-3 PDF from fixture data...")

        sample_data = _get_sample_form_data()

        try:
            result = generate_w3_pdf(sample_data)
            self.stdout.write(self.style.SUCCESS(
                f"Sample W-3 PDF generated:\n"
                f"  Path: {result['temp_path']}\n"
                f"  Size: {result['file_size'] / 1024:.1f} KB\n"
                f"  Pages: {result['page_count']}\n"
                f"  API: {result['api_number']}"
            ))
        except W3PDFGeneratorError as e:
            self.stderr.write(self.style.ERROR(f"Error: {e}"))


def _get_sample_form_data():
    """Return realistic sample W-3 form data for preview testing."""
    return {
        "header": {
            "api_number": "42-501-70575",
            "rrc_district": "03",
            "rrc_lease_id": "04567",
            "field_name": "BARNETT SHALE",
            "lease_name": "SMITH RANCH A",
            "well_number": "1H",
            "operator": "RegulAgent Demo Energy LLC",
            "county": "Tarrant",
            "operator_address": "123 Main St, Fort Worth, TX 76102",
            "original_w1_operator": "Original Drilling Co",
            "subsequent_w1_operator": "",
            "drilling_permit_date": "01/15/2020",
            "permit_number": "876543",
            "section_block_survey": "Sec 5, Blk 7, H&TC RR Co Survey, A-1234",
            "direction_from_town": "3.2 mi NE of Azle",
            "drilling_commenced": "02/01/2020",
            "well_type": "Gas",
            "total_depth": "12500",
            "drilling_completed": "03/15/2020",
            "date_well_plugged": "01/20/2025",
            "condensate_on_hand": "50 bbls",
            "mud_filled": True,
            "mud_application_method": "Circulated",
            "mud_weight_ppg": "9.5",
            "cementing_company": "Halliburton Energy Services",
            "date_rrc_notified": "01/10/2025",
            "surface_owners": "John Smith, 456 Ranch Road, Azle TX 76020",
            "all_wells_plugged": True,
            "location": {
                "feet_from_line1": "660",
                "feet_from_line2": "1320",
            },
        },
        "plugs": [
            {
                "plug_number": 1,
                "depth_top_ft": 5000,
                "depth_bottom_ft": 12500,
                "type": "cement_plug",
                "cement_class": "H",
                "sacks": 150,
                "slurry_weight_ppg": 15.8,
                "hole_size_in": 4.5,
                "calculated_top_of_plug_ft": 4200,
                "measured_top_of_plug_ft": 4180,
                "remarks": "Production plug across perforations",
                "cementing_date": "01/20/2025",
            },
            {
                "plug_number": 2,
                "depth_top_ft": 3000,
                "depth_bottom_ft": 5000,
                "type": "cement_plug",
                "cement_class": "H",
                "sacks": 100,
                "slurry_weight_ppg": 15.8,
                "hole_size_in": 4.5,
                "calculated_top_of_plug_ft": 2500,
                "measured_top_of_plug_ft": 2480,
                "remarks": "Intermediate plug",
                "cementing_date": "01/21/2025",
            },
            {
                "plug_number": 3,
                "depth_top_ft": 0,
                "depth_bottom_ft": 500,
                "type": "surface_plug",
                "cement_class": "C",
                "sacks": 50,
                "slurry_weight_ppg": 14.8,
                "hole_size_in": 8.75,
                "calculated_top_of_plug_ft": 0,
                "measured_top_of_plug_ft": None,
                "remarks": "Surface plug to ground level",
                "cementing_date": "01/22/2025",
            },
        ],
        "casing_record": [
            {
                "od_in": 13.375,
                "weight_ppf": 54.5,
                "top_ft": 0,
                "bottom_ft": 1200,
                "hole_size_in": 17.5,
                "removed_to_depth_ft": None,
                "setting_depth_ft": 1200,
            },
            {
                "od_in": 9.625,
                "weight_ppf": 40,
                "top_ft": 0,
                "bottom_ft": 8500,
                "hole_size_in": 12.25,
                "removed_to_depth_ft": None,
                "setting_depth_ft": 8500,
            },
            {
                "od_in": 5.5,
                "weight_ppf": 17,
                "top_ft": 0,
                "bottom_ft": 12500,
                "hole_size_in": 8.75,
                "removed_to_depth_ft": None,
                "setting_depth_ft": 12500,
            },
        ],
        "perforations": [
            {"from_ft": 11800, "to_ft": 11850, "status": "plugged"},
            {"from_ft": 11900, "to_ft": 11950, "status": "plugged"},
            {"from_ft": 12000, "to_ft": 12100, "status": "plugged"},
        ],
        "duqw": {
            "depth_ft": 500,
            "top_ft": 0,
            "bottom_ft": 500,
        },
        "remarks": "Well plugged per RRC Rule 14(b)(2). All perforations squeezed and verified. Surface plug placed to ground level with Class C cement.",
    }

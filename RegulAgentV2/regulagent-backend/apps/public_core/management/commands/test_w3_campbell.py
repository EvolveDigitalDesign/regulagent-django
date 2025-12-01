"""
Management command to test W-3 generation with Campbell 209 payload
"""
import json
import base64
import logging
from pathlib import Path
from io import BytesIO
from django.core.management.base import BaseCommand
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory

from apps.public_core.services.w3_builder import build_w3_from_pna_payload

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Test W-3 generation with Campbell 209 pnaexchange payload and W-3A PDF'

    def add_arguments(self, parser):
        parser.add_argument(
            '--payload',
            type=str,
            default='tmp/campbell_events.json',
            help='Path to campbell_events.json (relative to manage.py)'
        )
        parser.add_argument(
            '--w3a-pdf',
            type=str,
            default='tmp/W3A_Examples/Summit_Campbell_209_Approved_W-3A.pdf',
            help='Path to W-3A PDF file (relative to manage.py)'
        )

    def handle(self, *args, **options):
        payload_path = options['payload']
        w3a_pdf_path = options['w3a_pdf']

        self.stdout.write(f"Loading payload from: {payload_path}")
        self.stdout.write(f"Loading W-3A PDF from: {w3a_pdf_path}")

        # Load pnaexchange payload
        with open(payload_path, 'r') as f:
            pna_response = json.load(f)

        w3_form_data = pna_response.get('w3_form', {})
        well_data = w3_form_data.get('well', {})
        events = w3_form_data.get('events', [])

        self.stdout.write(f"\n✓ Loaded pnaexchange payload:")
        self.stdout.write(f"  - Well: {well_data.get('well_name')} ({well_data.get('api_number')})")
        self.stdout.write(f"  - Events: {len(events)}")

        # Load W-3A PDF
        with open(w3a_pdf_path, 'rb') as f:
            pdf_content = f.read()

        self.stdout.write(f"\n✓ Loaded W-3A PDF:")
        self.stdout.write(f"  - Size: {len(pdf_content)} bytes")

        # Create an uploaded file object
        uploaded_file = SimpleUploadedFile(
            name='Summit_Campbell_209_Approved_W-3A.pdf',
            content=pdf_content,
            content_type='application/pdf'
        )

        # Create a simple mock request object with FILES attribute
        class MockRequest:
            def __init__(self, pdf_file):
                self.FILES = {'w3a_file': pdf_file}
                self.POST = {}
        
        mock_request = MockRequest(uploaded_file)
        self.stdout.write(f"✓ Created mock request with PDF file")

        # Build payload for W-3 builder (flat structure)
        pna_payload = {
            'api_number': well_data.get('api_number'),
            'dwr_id': 355,  # From subproject ID
            'well_name': well_data.get('well_name'),
            'pna_events': events,
            'w3a_reference': {
                'type': 'pdf',
                'w3a_file': uploaded_file
            },
            'tenant_id': 1
        }

        self.stdout.write(f"\n" + "=" * 80)
        self.stdout.write("CALLING build_w3_from_pna_payload...")
        self.stdout.write("=" * 80)

        # Call the builder with mock request
        result = build_w3_from_pna_payload(pna_payload, request=mock_request)

        # Display results
        self.stdout.write(f"\n" + "=" * 80)
        self.stdout.write("RESULT")
        self.stdout.write("=" * 80)

        if result.get('success'):
            self.stdout.write(self.style.SUCCESS("✓ W-3 form generated successfully"))

            w3_form = result.get('w3_form', {})
            metadata = result.get('metadata', {})
            validation = result.get('validation', {})

            self.stdout.write(f"\nMetadata:")
            self.stdout.write(f"  - API: {metadata.get('api_number')}")
            self.stdout.write(f"  - DWR ID: {metadata.get('dwr_id')}")
            self.stdout.write(f"  - Events processed: {metadata.get('events_processed')}")
            self.stdout.write(f"  - Plugs grouped: {metadata.get('plugs_grouped')}")

            plugs = w3_form.get('plugs', [])
            self.stdout.write(f"\nPlugs ({len(plugs)}):")
            for plug in plugs:
                self.stdout.write(
                    f"  - Plug #{plug.get('plug_number')}: "
                    f"{plug.get('depth_top_ft')}-{plug.get('depth_bottom_ft')} ft, "
                    f"{plug.get('sacks')} sacks, "
                    f"Class {plug.get('cement_class')}"
                )

            if validation.get('warnings'):
                self.stdout.write(f"\nWarnings ({len(validation['warnings'])}):")
                for warning in validation['warnings']:
                    self.stdout.write(f"  - {warning}")

            if validation.get('errors'):
                self.stdout.write(f"\nErrors ({len(validation['errors'])}):")
                for error in validation['errors']:
                    self.stdout.write(f"  - {error}")

            # Show full JSON output
            self.stdout.write(f"\n" + "=" * 80)
            self.stdout.write("FULL JSON RESPONSE")
            self.stdout.write("=" * 80)
            self.stdout.write(json.dumps(result, indent=2, default=str))

        else:
            self.stdout.write(self.style.ERROR(f"✗ W-3 generation failed: {result.get('error')}"))
            self.stdout.write(json.dumps(result, indent=2, default=str))


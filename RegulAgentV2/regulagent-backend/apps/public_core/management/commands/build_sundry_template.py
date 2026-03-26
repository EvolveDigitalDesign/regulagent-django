"""
Django management command: build_sundry_template

Stamps AcroForm widgets onto the blank BLM 3160-5 template, producing an
annotated template that the PDF generator can fill by widget name.

Usage:
    python manage.py build_sundry_template
    python manage.py build_sundry_template --output /path/to/output.pdf
    python manage.py build_sundry_template --verify
    python manage.py build_sundry_template --verify --output /path/to/template.pdf
    python manage.py build_sundry_template --verify-only
    python manage.py build_sundry_template --verify-only --output /path/to/template.pdf
"""

from pathlib import Path

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Build the annotated BLM 3160-5 template with named AcroForm widgets"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            help="Output path for annotated template",
        )
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Verify widget counts after building",
        )
        parser.add_argument(
            "--verify-only",
            action="store_true",
            help="Only verify existing template, don't rebuild",
        )

    def handle(self, *args, **options):
        try:
            from apps.public_core.services.sundry_template_builder import (
                build_annotated_template,
                verify_template,
                _ANNOTATED_TEMPLATE_PATH,
            )
        except ImportError as e:
            self.stderr.write(self.style.ERROR(
                f"Failed to import sundry_template_builder (is PyMuPDF installed?): {e}"
            ))
            return

        output_path = Path(options["output"]) if options.get("output") else None

        if not options.get("verify_only"):
            # Build the template
            try:
                result_path = build_annotated_template(output_path=output_path)
                self.stdout.write(self.style.SUCCESS(
                    f"Annotated template saved to: {result_path}"
                ))
            except FileNotFoundError as e:
                self.stderr.write(self.style.ERROR(
                    f"Template file not found: {e}"
                ))
                return
            except Exception as e:
                self.stderr.write(self.style.ERROR(
                    f"Failed to build annotated template: {e}"
                ))
                return

        if options.get("verify") or options.get("verify_only"):
            verify_path = output_path or _ANNOTATED_TEMPLATE_PATH

            if not verify_path.exists():
                self.stderr.write(self.style.ERROR(
                    f"Template file does not exist at: {verify_path}\n"
                    f"Run without --verify-only to build the template first."
                ))
                return

            try:
                info = verify_template(verify_path)
            except FileNotFoundError as e:
                self.stderr.write(self.style.ERROR(
                    f"Template file not found during verification: {e}"
                ))
                return
            except Exception as e:
                self.stderr.write(self.style.ERROR(
                    f"Verification failed: {e}"
                ))
                return

            self.stdout.write(f"\nTemplate verification:")
            self.stdout.write(f"  Total widgets:  {info['total_widgets']}")
            self.stdout.write(f"  Page 1 widgets: {info['page_1_widgets']}")
            self.stdout.write(f"\nWidget names ({info['total_widgets']}):")
            for name in sorted(info["widget_names"]):
                self.stdout.write(f"    {name}")
            self.stdout.write(self.style.SUCCESS("Verification complete."))

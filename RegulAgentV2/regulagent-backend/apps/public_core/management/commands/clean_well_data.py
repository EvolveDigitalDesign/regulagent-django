"""
Management command to clean all data for a well (by 10-digit API number).

Useful for re-running well analysis with updated extraction logic without
hitting the 14-day cache limit in RRC document fetching.

Usage:
    python manage.py clean_well_data --api 4200301016 [--tenant TENANT_UUID]
    python manage.py clean_well_data --api 42003001016 --dry-run
    python manage.py clean_well_data --api 4200301016 --force
"""

import logging
import shutil
from pathlib import Path
from typing import Optional
from uuid import UUID
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.conf import settings
from apps.public_core.models import (
    WellRegistry,
    ExtractedDocument,
    PlanSnapshot,
    DocumentVector,
    PublicFacts,
    PublicWellDepths,
    PublicCasingString,
    PublicPerforation,
    PublicArtifacts,
    W3EventORM,
    W3PlugORM,
    W3FormORM,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Clean all data for a well (WellRegistry + all related records). "
        "Useful for re-running analysis with updated logic. "
        "Also clears RRC PDF cache to force fresh extraction."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--api',
            type=str,
            required=True,
            help='10-digit or 14-digit API number (e.g., 4200301016 or 42-003-01016)',
        )
        parser.add_argument(
            '--tenant',
            type=str,
            required=False,
            help='Optional tenant UUID filter (for multi-tenant isolation)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip confirmation prompt',
        )
        parser.add_argument(
            '--keep-pdfs',
            action='store_true',
            help='Keep cached PDF files in media directory (only delete DB records)',
        )

    def handle(self, *args, **options):
        api_input = options['api']
        tenant_uuid = options.get('tenant')
        dry_run = options.get('dry_run', False)
        force = options.get('force', False)
        keep_pdfs = options.get('keep_pdfs', False)

        self.stdout.write(self.style.HTTP_INFO("\n" + "=" * 80))
        self.stdout.write(self.style.HTTP_INFO("🗑️  WELL DATA CLEANUP"))
        self.stdout.write(self.style.HTTP_INFO("=" * 80))

        # Normalize API number
        normalized_api = self._normalize_api(api_input)
        if not normalized_api:
            raise CommandError(
                f"❌ Invalid API number: {api_input}. "
                f"Must be 8, 10, or 14 digits."
            )

        self.stdout.write(
            f"📍 API number: {api_input} → normalized to {normalized_api}"
        )

        if tenant_uuid:
            self.stdout.write(f"🏢 Tenant UUID: {tenant_uuid}")
            try:
                UUID(tenant_uuid)
            except ValueError:
                raise CommandError(f"❌ Invalid tenant UUID: {tenant_uuid}")

        # Find the well
        well = self._find_well(normalized_api)
        if not well:
            self.stdout.write(
                self.style.WARNING(
                    f"\n⚠️  No well found for API: {normalized_api}\n"
                    f"    (Checked WellRegistry with api14 containing: {normalized_api[-8:]})"
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(f"\n✅ Found well: {well.api14} ({well.operator_name or 'Unknown'})")
        )

        # Count what will be deleted
        counts = self._count_records(well, normalized_api, tenant_uuid)
        self.stdout.write(self._format_counts(counts, dry_run))

        # Show confirmation
        if not dry_run and not force:
            if not self._confirm_deletion():
                self.stdout.write(self.style.WARNING("⏸️  Cleanup cancelled."))
                return

        # Perform deletion
        if dry_run:
            self.stdout.write(self.style.WARNING("\n📋 DRY RUN MODE - No changes made"))
        else:
            self.stdout.write(self.style.WARNING("\n🔥 DELETING..."))
            deleted_count = self._delete_records(well, normalized_api, tenant_uuid)
            self.stdout.write(
                self.style.SUCCESS(f"\n✅ Deleted {deleted_count} database records")
            )

            # Clean up PDF cache if requested
            if not keep_pdfs:
                pdf_deleted = self._delete_pdf_cache(normalized_api)
                if pdf_deleted:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"🗑️  Deleted {pdf_deleted} cached PDF files"
                        )
                    )
                else:
                    self.stdout.write("ℹ️  No cached PDF files to delete")
            else:
                self.stdout.write("⏭️  Keeping cached PDF files (--keep-pdfs)")

        self.stdout.write(self.style.SUCCESS("\n" + "=" * 80))
        self.stdout.write(
            self.style.SUCCESS(
                "✅ CLEANUP COMPLETE\n"
                "   You can now run W-3A generation with fresh extraction logic.\n"
                "   RRC PDFs will be re-downloaded and re-extracted."
            )
        )
        self.stdout.write(self.style.SUCCESS("=" * 80 + "\n"))

    def _normalize_api(self, api_str: str) -> Optional[str]:
        """Normalize API number to 14-digit format"""
        import re
        # Remove all non-digits
        digits = re.sub(r'\D', '', str(api_str or ''))

        # Validate length
        if len(digits) not in (8, 10, 14):
            return None

        # If 8 digits, assume TX (42) + county (003) + well (01016) → 42-003-01016
        if len(digits) == 8:
            return f"42003{digits}"
        elif len(digits) == 10:
            # Assume missing leading 42 (TX)
            return f"42{digits}"
        else:
            # Already 14 digits
            return digits

    def _find_well(self, normalized_api: str) -> Optional[WellRegistry]:
        """Find well by API number"""
        # Try exact match first
        well = WellRegistry.objects.filter(api14=normalized_api).first()
        if well:
            return well

        # Try last 8 digits
        well = WellRegistry.objects.filter(
            api14__icontains=normalized_api[-8:]
        ).first()
        if well:
            return well

        return None

    def _count_records(
        self,
        well: WellRegistry,
        normalized_api: str,
        tenant_uuid: Optional[str] = None,
    ) -> dict:
        """Count how many records will be deleted"""
        tenant_filter = {}
        if tenant_uuid:
            tenant_filter['uploaded_by_tenant'] = tenant_uuid

        return {
            'extracted_documents': ExtractedDocument.objects.filter(
                well=well, **tenant_filter
            ).count(),
            'document_vectors': DocumentVector.objects.filter(
                well=well
            ).count(),
            'plan_snapshots': PlanSnapshot.objects.filter(
                well=well,
                tenant_id=tenant_uuid or None
            ).count(),
            'public_facts': PublicFacts.objects.filter(well=well).count(),
            'public_well_depths': PublicWellDepths.objects.filter(well=well).count(),
            'public_casing_strings': PublicCasingString.objects.filter(well=well).count(),
            'public_perforations': PublicPerforation.objects.filter(well=well).count(),
            'public_artifacts': PublicArtifacts.objects.filter(well=well).count(),
            'w3_events': W3EventORM.objects.filter(
                api_number__icontains=normalized_api[-8:]
            ).count(),
            'w3_plugs': W3PlugORM.objects.filter(
                api_number__icontains=normalized_api[-8:]
            ).count(),
            'w3_forms': W3FormORM.objects.filter(
                api_number__icontains=normalized_api[-8:]
            ).count(),
        }

    def _format_counts(self, counts: dict, dry_run: bool = False) -> str:
        """Format counts for display"""
        total = sum(counts.values())
        prefix = "🔍 WOULD DELETE" if dry_run else "📊 WILL DELETE"

        output = f"\n{prefix} ({total} records):\n"
        for model, count in counts.items():
            if count > 0:
                output += f"  • {model}: {count}\n"

        return output

    def _confirm_deletion(self) -> bool:
        """Ask user for confirmation"""
        self.stdout.write(
            self.style.WARNING(
                "\n⚠️  WARNING: This will permanently delete all data for this well.\n"
                "   This includes:\n"
                "   - All extracted documents (W-2, W-15, GAU, etc.)\n"
                "   - All plan snapshots (W-3A plans)\n"
                "   - All cached PDFs\n"
                "   - All W-3 forms and events\n"
            )
        )
        response = input("Are you sure? (type 'yes' to confirm): ")
        return response.lower() == 'yes'

    @transaction.atomic
    def _delete_records(
        self,
        well: WellRegistry,
        normalized_api: str,
        tenant_uuid: Optional[str] = None,
    ) -> int:
        """Delete all records for the well"""
        deleted_count = 0
        tenant_filter = {}
        if tenant_uuid:
            tenant_filter['uploaded_by_tenant'] = tenant_uuid

        # Delete W-3 records first (don't depend on WellRegistry foreign key)
        count, _ = W3EventORM.objects.filter(
            api_number__icontains=normalized_api[-8:]
        ).delete()
        deleted_count += count
        self.stdout.write(f"  ✓ Deleted W3 events")

        count, _ = W3PlugORM.objects.filter(
            api_number__icontains=normalized_api[-8:]
        ).delete()
        deleted_count += count
        self.stdout.write(f"  ✓ Deleted W3 plugs")

        count, _ = W3FormORM.objects.filter(
            api_number__icontains=normalized_api[-8:]
        ).delete()
        deleted_count += count
        self.stdout.write(f"  ✓ Deleted W3 forms")

        # Delete everything linked to WellRegistry (cascade handles most)
        # These are listed for clarity, but cascade does the work
        count, _ = ExtractedDocument.objects.filter(
            well=well, **tenant_filter
        ).delete()
        deleted_count += count
        self.stdout.write(f"  ✓ Deleted extracted documents (includes cascade)")

        count, _ = PlanSnapshot.objects.filter(
            well=well,
            tenant_id=tenant_uuid or None
        ).delete()
        deleted_count += count
        self.stdout.write(f"  ✓ Deleted plan snapshots")

        count, _ = PublicFacts.objects.filter(well=well).delete()
        deleted_count += count
        self.stdout.write(f"  ✓ Deleted public facts")

        count, _ = PublicWellDepths.objects.filter(well=well).delete()
        deleted_count += count
        self.stdout.write(f"  ✓ Deleted well depths")

        count, _ = PublicCasingString.objects.filter(well=well).delete()
        deleted_count += count
        self.stdout.write(f"  ✓ Deleted casing strings")

        count, _ = PublicPerforation.objects.filter(well=well).delete()
        deleted_count += count
        self.stdout.write(f"  ✓ Deleted perforations")

        count, _ = PublicArtifacts.objects.filter(well=well).delete()
        deleted_count += count
        self.stdout.write(f"  ✓ Deleted artifacts")

        return deleted_count

    def _delete_pdf_cache(self, normalized_api: str) -> int:
        """Delete cached PDF files"""
        try:
            media_root = getattr(settings, 'MEDIA_ROOT', '.')
            pdf_dir = Path(media_root) / normalized_api

            if not pdf_dir.exists():
                return 0

            pdf_files = list(pdf_dir.glob('*.pdf'))
            pdf_count = len(pdf_files)

            if pdf_count > 0:
                shutil.rmtree(pdf_dir)
                self.stdout.write(f"  ✓ Deleted {pdf_count} PDF files from cache")
                return pdf_count

            return 0
        except Exception as e:
            logger.warning(f"Failed to delete PDF cache: {e}")
            self.stdout.write(
                self.style.WARNING(f"  ⚠️  Could not delete PDF cache: {e}")
            )
            return 0


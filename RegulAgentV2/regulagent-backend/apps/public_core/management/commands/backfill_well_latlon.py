from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.public_core.models import WellRegistry, ExtractedDocument


class Command(BaseCommand):
    help = "Backfill WellRegistry lat/lon/operator_name/field_name from latest W-2 JSON."

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=500, help='Max wells to process (0 for all).')

    def handle(self, *args, **options):
        limit = int(options.get('limit') or 0)
        qs = WellRegistry.objects.all().order_by('created_at')
        if limit > 0:
            qs = qs[:limit]
        updated = 0
        for well in qs:
            api = well.api14
            w2 = (
                ExtractedDocument.objects
                .filter(api_number__icontains=str(api)[-8:], document_type='w2')
                .order_by('-created_at')
                .first()
            )
            if not w2 or not isinstance(w2.json_data, dict):
                continue
            wi = (w2.json_data.get('well_info') or {})
            loc = wi.get('location') or {}
            lat = loc.get('lat') or loc.get('latitude')
            lon = loc.get('lon') or loc.get('longitude')
            operator = wi.get('operator') or wi.get('operator_name')
            field_name = wi.get('field') or wi.get('field_name')
            try:
                lat_f = float(lat) if lat is not None else None
                lon_f = float(lon) if lon is not None else None
            except Exception:
                lat_f = None; lon_f = None
            if (lat_f is not None and lon_f is not None) or operator or field_name:
                with transaction.atomic():
                    changed = False
                    if (lat_f is not None and lon_f is not None) and (well.lat is None or well.lon is None):
                        well.lat = lat_f
                        well.lon = lon_f
                        changed = True
                    if operator and not (well.operator_name or '').strip():
                        well.operator_name = str(operator)[:128]
                        changed = True
                    if field_name and not (well.field_name or '').strip():
                        well.field_name = str(field_name)[:128]
                        changed = True
                    if changed:
                        well.save(update_fields=['lat', 'lon', 'operator_name', 'field_name', 'updated_at'])
                        updated += 1
        self.stdout.write(self.style.SUCCESS(f"Backfilled lat/lon for {updated} wells"))



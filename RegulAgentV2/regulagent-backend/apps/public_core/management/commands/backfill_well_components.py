from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.public_core.models import WellRegistry, WellComponent
from apps.public_core.models.public_casing_string import PublicCasingString
from apps.public_core.models.public_perforation import PublicPerforation
from apps.public_core.models.plan_snapshot import PlanSnapshot

BATCH_SIZE = 500


def _to_decimal(val) -> Decimal | None:
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, TypeError):
        return None

PLAN_STEP_TYPE_MAP = {
    "cement_plug": "cement_plug",
    "bridge_plug": "bridge_plug",
    "bridge_plug_cap": "cement_plug",
}


class Command(BaseCommand):
    help = "Backfill WellComponent records from existing PublicCasingString, PublicPerforation, and PlanSnapshot data."

    def add_arguments(self, parser):
        parser.add_argument("--source", choices=["all", "public", "plans"], default="all")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--well", type=str, help="Filter by api14")

    def handle(self, *args, **options):
        source = options["source"]
        dry_run = options["dry_run"]
        api14_filter = options.get("well")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no records will be written"))

        total_casing = 0
        total_perf = 0
        total_plan = 0

        if source in ("all", "public"):
            casing, perf = self._backfill_public(dry_run, api14_filter)
            total_casing += casing
            total_perf += perf

        if source in ("all", "plans"):
            total_plan += self._backfill_plans(dry_run, api14_filter)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {total_casing} casing, {total_perf} perforation, {total_plan} plan_proposed components"
            )
        )

    # ------------------------------------------------------------------
    # Public layer: PublicCasingString + PublicPerforation
    # ------------------------------------------------------------------

    def _backfill_public(self, dry_run: bool, api14_filter: str | None) -> tuple[int, int]:
        well_qs = WellRegistry.objects.all()
        if api14_filter:
            well_qs = well_qs.filter(api14=api14_filter)

        total_casing = 0
        total_perf = 0
        batch: list[WellComponent] = []

        for idx, well in enumerate(well_qs.iterator(chunk_size=200)):
            if idx > 0 and idx % 100 == 0:
                self.stdout.write(f"  public: processed {idx} wells...")

            # Skip wells that already have public layer components
            if WellComponent.objects.filter(well=well, layer=WellComponent.Layer.PUBLIC).exists():
                continue

            # Map PublicCasingString records
            for cs in PublicCasingString.objects.filter(well=well):
                batch.append(WellComponent(
                    well=well,
                    component_type=WellComponent.ComponentType.CASING,
                    layer=WellComponent.Layer.PUBLIC,
                    lifecycle_state=WellComponent.LifecycleState.INSTALLED,
                    sort_order=cs.string_no,
                    outside_dia_in=cs.outside_dia_in,
                    weight_ppf=cs.weight_ppf,
                    grade=cs.grade or "",
                    thread_type=cs.thread_type or "",
                    top_ft=cs.top_ft,
                    bottom_ft=cs.shoe_ft,
                    cement_top_ft=cs.cement_to_ft,
                    provenance=cs.provenance,
                    source_document_type=cs.source or "",
                    as_of=cs.as_of,
                    properties={"string_no": cs.string_no},
                ))
                total_casing += 1

            # Map PublicPerforation records
            for perf in PublicPerforation.objects.filter(well=well):
                props: dict = {}
                if perf.formation:
                    props["formation"] = perf.formation
                if perf.shot_density_spf is not None:
                    props["shot_density_spf"] = float(perf.shot_density_spf)
                if perf.phase_deg is not None:
                    props["phase_deg"] = float(perf.phase_deg)

                batch.append(WellComponent(
                    well=well,
                    component_type=WellComponent.ComponentType.PERFORATION,
                    layer=WellComponent.Layer.PUBLIC,
                    lifecycle_state=WellComponent.LifecycleState.INSTALLED,
                    top_ft=perf.top_ft,
                    bottom_ft=perf.bottom_ft,
                    provenance=perf.provenance,
                    source_document_type=perf.source or "",
                    as_of=perf.as_of,
                    properties=props,
                ))
                total_perf += 1

            if len(batch) >= BATCH_SIZE:
                if not dry_run:
                    with transaction.atomic():
                        WellComponent.objects.bulk_create(batch, ignore_conflicts=True)
                batch = []

        # Flush remaining
        if batch and not dry_run:
            with transaction.atomic():
                WellComponent.objects.bulk_create(batch, ignore_conflicts=True)

        self.stdout.write(f"  public: {total_casing} casing, {total_perf} perforation")
        return total_casing, total_perf

    # ------------------------------------------------------------------
    # Plan layer: PlanSnapshot.payload.steps → WellComponent(layer="plan_proposed")
    # ------------------------------------------------------------------

    def _backfill_plans(self, dry_run: bool, api14_filter: str | None) -> int:
        snap_qs = PlanSnapshot.objects.filter(payload__has_key="steps")
        if api14_filter:
            snap_qs = snap_qs.filter(well__api14=api14_filter)

        total_plan = 0
        batch: list[WellComponent] = []

        for idx, snap in enumerate(snap_qs.select_related("well", "workspace").iterator(chunk_size=200)):
            if idx > 0 and idx % 100 == 0:
                self.stdout.write(f"  plans: processed {idx} snapshots...")

            if snap.well is None:
                continue

            # Skip snapshots whose well already has plan_proposed components
            if WellComponent.objects.filter(
                well=snap.well,
                layer=WellComponent.Layer.PLAN_PROPOSED,
                plan_snapshot=snap,
            ).exists():
                continue

            steps = snap.payload.get("steps") or []
            if not isinstance(steps, list):
                continue

            for step in steps:
                if not isinstance(step, dict):
                    continue

                raw_type = step.get("type", "")
                component_type = PLAN_STEP_TYPE_MAP.get(raw_type)
                if component_type is None:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  plans: unknown step type '{raw_type}' in snapshot {snap.id}, skipping"
                        )
                    )
                    continue

                props: dict = {}
                for key in ("regulatory_basis", "placement_basis", "geometry_context"):
                    if step.get(key) is not None:
                        props[key] = step[key]

                batch.append(WellComponent(
                    well=snap.well,
                    component_type=component_type,
                    layer=WellComponent.Layer.PLAN_PROPOSED,
                    lifecycle_state=WellComponent.LifecycleState.PROPOSED_ADDITION,
                    plan_snapshot=snap,
                    tenant_id=snap.tenant_id,
                    workspace=snap.workspace,
                    top_ft=_to_decimal(step.get("top_ft")),
                    bottom_ft=_to_decimal(step.get("bottom_ft")),
                    depth_ft=_to_decimal(step.get("depth_ft")),
                    sacks=_to_decimal(step.get("sacks")),
                    cement_class=step.get("cement_class") or "",
                    source_document_type="plan_snapshot",
                    provenance={"plan_snapshot_id": str(snap.id)},
                    properties=props,
                ))
                total_plan += 1

            if len(batch) >= BATCH_SIZE:
                if not dry_run:
                    with transaction.atomic():
                        WellComponent.objects.bulk_create(batch, ignore_conflicts=True)
                batch = []

        # Flush remaining
        if batch and not dry_run:
            with transaction.atomic():
                WellComponent.objects.bulk_create(batch, ignore_conflicts=True)

        self.stdout.write(f"  plans: {total_plan} plan_proposed components")
        return total_plan

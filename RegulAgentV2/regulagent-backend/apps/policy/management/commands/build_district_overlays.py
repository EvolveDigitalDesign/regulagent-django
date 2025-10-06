from django.core.management.base import BaseCommand
import os
import yaml
from typing import Any, Dict

from apps.policy.services.district_overlay_builder import load_json, build_overlay_from_plugging_book


class Command(BaseCommand):
    help = "Build district overlay YAMLs from district plugging book JSON files"

    def add_arguments(self, parser):
        parser.add_argument("district_code", type=str, help="District code, e.g., 7C or 08A")
        parser.add_argument("json_path", type=str, help="Path to district plugging book JSON")
        parser.add_argument("output_yaml", type=str, help="Output YAML path to write overlay")
        parser.add_argument("--dry-run", action="store_true", help="Do not write file; print summary")

    def handle(self, *args, **options):
        district = options["district_code"]
        json_path = options["json_path"]
        output_yaml = options["output_yaml"]
        dry_run = options["dry_run"]

        data = load_json(json_path)
        overlay: Dict[str, Any] = build_overlay_from_plugging_book(data, district)

        if dry_run:
            counties = overlay.get("counties", {})
            def has_req(key: str) -> int:
                return sum(1 for c in counties.values() if c.get("requirements", {}).get(key))
            def has_ovr(key: str) -> int:
                return sum(1 for c in counties.values() if c.get("overrides", {}).get(key))
            cap_ct = has_req("cap_above_highest_perf_ft")
            cibp_ct = has_req("cement_above_cibp_min_ft")
            tag_ct = has_req("tagging_required_hint")
            shoe_sym_ct = has_req("surface_shoe_symmetry_ft")
            tubing_only_ct = has_req("pump_through_tubing_or_drillpipe_only")
            perf_packer_ct = has_req("perforate_and_pump_under_packer_if_casing_not_recovered")
            duqw_extra_ct = has_req("additional_surface_inside_plug_len_ft_if_below_duqw")

            # Overrides summary
            wbl_ct = has_ovr("wbl")
            protect_ct = has_ovr("protect_intervals")
            tag_over_ct = has_ovr("tag")
            squeeze_ct = has_ovr("squeeze")
            combine_ct = has_ovr("combine_formations")
            er_ct = has_ovr("enhanced_recovery_zone")
            mig_ct = has_ovr("migration_risk")
            ftop_ct = has_ovr("formation_tops")

            self.stdout.write(self.style.SUCCESS(
                f"Derived counties: {len(counties)}\n"
                f"  requirements:\n"
                f"    cap_above_highest_perf_ft: {cap_ct}\n"
                f"    cement_above_cibp_min_ft: {cibp_ct}\n"
                f"    tagging_required_hint: {tag_ct}\n"
                f"    surface_shoe_symmetry_ft: {shoe_sym_ct}\n"
                f"    pump_through_tubing_or_drillpipe_only: {tubing_only_ct}\n"
                f"    perforate_and_pump_under_packer_if_casing_not_recovered: {perf_packer_ct}\n"
                f"    additional_surface_inside_plug_len_ft_if_below_duqw: {duqw_extra_ct}\n"
                f"  overrides:\n"
                f"    wbl: {wbl_ct}\n"
                f"    protect_intervals: {protect_ct}\n"
                f"    tag: {tag_over_ct}\n"
                f"    squeeze: {squeeze_ct}\n"
                f"    combine_formations: {combine_ct}\n"
                f"    enhanced_recovery_zone: {er_ct}\n"
                f"    migration_risk: {mig_ct}\n"
                f"    formation_tops: {ftop_ct}"
            ))
            # sample county detail
            for cname, c in counties.items():
                sample = {
                    "requirements": c.get("requirements") or {},
                    "overrides": c.get("overrides") or {},
                }
                self.stdout.write(f"Sample county: {cname}: {yaml.safe_dump(sample, sort_keys=False)}")
                break
            return

        os.makedirs(os.path.dirname(output_yaml) or ".", exist_ok=True)
        with open(output_yaml, "w", encoding="utf-8") as f:
            yaml.safe_dump(overlay, f, sort_keys=False, allow_unicode=True)
        self.stdout.write(self.style.SUCCESS(f"Wrote overlay to {output_yaml}"))


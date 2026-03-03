from django.core.management.base import BaseCommand
import os
import json
import yaml
from typing import Any, Dict

from apps.policy.services.district_overlay_builder import (
    load_json, 
    build_overlay_from_plugging_book, 
    normalize_7c_to_standard,
    _derive_requirements_from_notes,
    _derive_overrides_from_notes
)


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

        raw_data = load_json(json_path)
        
        # For 7C, use hybrid approach: split into YAML (procedures) + JSON (formations)
        use_hybrid = district.upper() in ['7C']
        
        if use_hybrid:
            # Keep raw data for JSON, normalize for YAML procedures
            normalized_data = normalize_7c_to_standard(raw_data)
            overlay = self._build_hybrid_7c(normalized_data, raw_data, district, output_yaml, dry_run)
            if dry_run:
                self._print_hybrid_summary(overlay)
            return
        
        # Normalize for standard approach
        data = normalize_7c_to_standard(raw_data)
        
        # Standard approach for other districts (8A, etc.)
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
    
    def _build_hybrid_7c(self, normalized_data: Dict[str, Any], raw_data: Dict[str, Any], district: str, output_yaml: str, dry_run: bool) -> Dict[str, Any]:
        """Build hybrid 7C structure: YAML for procedures (normalized), JSON for formations (raw)."""
        from datetime import datetime
        
        # Build county procedures YAML (no formation data)
        county_procedures_yaml = {
            "district": district,
            "extracted_at": datetime.utcnow().isoformat() + "Z",
            "requirements": {},
            "preferences": {},
            "counties": {}
        }
        
        # Extract district-wide requirements from generalProcedures (use normalized)
        gen = normalized_data.get("generalProcedures") or []
        gtext = " ".join([gp.get("text") for gp in gen if isinstance(gp, dict) and gp.get("text")])
        if gtext:
            dist_reqs, dist_prefs = _derive_requirements_from_notes([gtext])
            county_procedures_yaml["requirements"] = dist_reqs
            county_procedures_yaml["preferences"] = dist_prefs
        
        # Extract plugging chart (use normalized)
        chart = normalized_data.get("pluggingChart") or {}
        if chart:
            county_procedures_yaml["preferences"]["plugging_chart"] = chart
        
        # Extract county-specific procedures from normalized data (NO formation data)
        for county_key, county in (normalized_data.get("counties") or {}).items():
            notes = county.get("notes") or []
            req, prefs = _derive_requirements_from_notes(notes)
            overrides = _derive_overrides_from_notes(notes)
            
            # Remove formation_tops from overrides (will be in JSON instead)
            county_procs = overrides.get("county_procedures", {})
            
            county_procedures_yaml["counties"][county.get("name", county_key)] = {
                "requirements": req,
                "preferences": prefs,
                "county_procedures": county_procs
            }
        
        # Build formation data JSON from RAW data, but normalize field specs for consistency
        formation_data_json = {
            "metadata": raw_data.get("metadata", {}),
            "district": district,
            "extracted_at": datetime.utcnow().isoformat() + "Z",
            "counties": {}
        }
        
        for county_key, county in (raw_data.get("counties") or {}).items():
            # Normalize fieldSpecs to handle both "field"/"formation" and "field_name"/"formations" formats
            # IMPORTANT: Handle state machine structure where use_when-only entries are headers
            raw_specs = county.get("fieldSpecs", [])
            normalized_specs = []
            
            # State machine: track current use_when context
            current_use_when = ""
            
            for spec in raw_specs:
                # Check if this is a use_when HEADER (has use_when but NO formations)
                has_formation = bool(spec.get("formation") or spec.get("formations"))
                has_use_when = bool(spec.get("use_when"))
                
                if has_use_when and not has_formation:
                    # This is a HEADER - update context for subsequent formations
                    current_use_when = spec.get("use_when", "")
                    continue  # Don't create a formation entry for headers
                
                # Skip entries with no formation data
                if not has_formation:
                    continue
                
                # Handle both formats: some counties use "field"/"formation", others use "field_name"/"formations"
                field_val = spec.get("field") or spec.get("field_name")
                formation_val = spec.get("formation") or spec.get("formations")
                
                normalized_spec = {
                    "field": field_val,
                    "formation": formation_val,
                    "tops": spec.get("tops"),
                }
                
                # use_when: prioritize explicit use_when on formation, else use current context from header
                effective_use_when = spec.get("use_when") or current_use_when
                if effective_use_when:
                    normalized_spec["use_when"] = effective_use_when
                
                # Preserve additional_requirements
                if spec.get("additional_requirements"):
                    normalized_spec["additional_requirements"] = spec["additional_requirements"]
                
                normalized_specs.append(normalized_spec)
            
            formation_data_json["counties"][county_key] = {
                "name": county.get("name"),
                "fieldSpecs": normalized_specs
            }
        
        # Write files (if not dry-run)
        if not dry_run:
            # Write county procedures YAML
            yaml_path = output_yaml.replace("__auto.yml", "_county_procedures.yml")
            os.makedirs(os.path.dirname(yaml_path) or ".", exist_ok=True)
            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(county_procedures_yaml, f, sort_keys=False, allow_unicode=True)
            self.stdout.write(self.style.SUCCESS(f"Wrote county procedures to {yaml_path}"))
            
            # Write formation data JSON
            json_output_path = output_yaml.replace("__auto.yml", "_plugging_book.json")
            with open(json_output_path, "w", encoding="utf-8") as f:
                json.dump(formation_data_json, f, indent=2)
            self.stdout.write(self.style.SUCCESS(f"Wrote formation data to {json_output_path}"))
        
        return {
            "yaml": county_procedures_yaml,
            "json": formation_data_json
        }
    
    def _print_hybrid_summary(self, overlay: Dict[str, Any]) -> None:
        """Print summary of hybrid 7C extraction."""
        yaml_data = overlay.get("yaml", {})
        json_data = overlay.get("json", {})
        
        counties_yaml = yaml_data.get("counties", {})
        counties_json = json_data.get("counties", {})
        
        self.stdout.write(self.style.SUCCESS(f"Hybrid 7C Extraction:"))
        self.stdout.write(f"  Counties: {len(counties_yaml)}")
        self.stdout.write(f"  ")
        self.stdout.write(self.style.WARNING("County Procedures YAML:"))
        self.stdout.write(f"    District requirements: {len(yaml_data.get('requirements', {}))}")
        self.stdout.write(f"    District preferences: {len(yaml_data.get('preferences', {}))}")
        
        # Sample county
        for county_name, county_data in list(counties_yaml.items())[:1]:
            self.stdout.write(f"  ")
            self.stdout.write(f"  Sample county: {county_name}")
            county_procs = county_data.get("county_procedures", {})
            self.stdout.write(f"    Procedures extracted: {list(county_procs.keys())}")
        
        self.stdout.write(f"  ")
        self.stdout.write(self.style.WARNING("Formation Data JSON:"))
        for county_key, county_data in list(counties_json.items())[:1]:
            field_specs = county_data.get("fieldSpecs", [])
            self.stdout.write(f"    Sample: {county_data.get('name')} - {len(field_specs)} formation specs")
            if field_specs:
                sample_spec = field_specs[0]
                # Use 7C keys: field, formation, tops
                field_name = sample_spec.get('field', 'N/A')
                formation = sample_spec.get('formation', 'N/A')
                tops = sample_spec.get('tops', 'N/A')
                use_when = sample_spec.get('use_when', '')
                additional_req = sample_spec.get('additional_requirements', '')
                self.stdout.write(f"      Example: {field_name} - {formation} @ {tops} ft")
                if use_when:
                    self.stdout.write(f"        use_when: {use_when}")
                if additional_req:
                    self.stdout.write(f"        additional_requirements: {additional_req}")


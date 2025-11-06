from __future__ import annotations

from typing import Any, Dict
import os
import json

from django.core.management.base import BaseCommand, CommandParser
import logging

logger = logging.getLogger(__name__)

from apps.public_core.models import ExtractedDocument
from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts


def wrap(v: Any) -> Dict[str, Any]:
    return {"value": v}


class Command(BaseCommand):
    help = "Build a kernel plan from latest GAU/W-2/W-15 extractions for an API and print a summary JSON."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--api", dest="api", default=None, help="API number (8/10/14-digit). If omitted, use latest available.")

    def handle(self, *args: Any, **options: Any) -> None:
        api = options.get("api")
        if not api:
            qs = (
                ExtractedDocument.objects
                .filter(document_type__in=["w2", "w15", "gau"])  # measured set
                .order_by("-created_at")
                .values_list("api_number", flat=True)
                .distinct()
            )
            if not qs:
                self.stdout.write("{}")
                return
            api = qs[0]

        def latest(doc_type: str) -> ExtractedDocument | None:
            return (
                ExtractedDocument.objects
                .filter(api_number=api, document_type=doc_type)
                .order_by("-created_at")
                .first()
            )

        w2_doc = latest("w2")
        gau_doc = latest("gau")
        w15_doc = latest("w15")
        w2 = (w2_doc and w2_doc.json_data) or {}
        gau = (gau_doc and gau_doc.json_data) or {}
        w15 = (w15_doc and w15_doc.json_data) or {}

        wi = w2.get("well_info") or {}
        api14 = (wi.get("api") or "").replace("-", "")
        county = wi.get("county") or ""
        field = wi.get("field") or ""
        lease = wi.get("lease") or ""
        well_no = wi.get("well_no") or ""
        # Prefer normalized district key; fall back to legacy rrc_district
        rrc = (wi.get("district") or wi.get("rrc_district") or "").strip()
        district = "08A" if (rrc in ("08", "8") and ("andrews" in county.lower())) else (rrc or "08A")

        # GAU base: use only if GAU exists and is not older than 5 years; else do not fallback
        uqw_depth = None
        uqw_source = None
        uqw_age_days: int | None = None
        try:
            import datetime as _dt
            gau_date_txt = ((gau.get("header") or {}).get("date") if gau else None) or None
            gau_depth = (gau.get("surface_casing_determination") or {}).get("gau_groundwater_protection_determination_depth") if gau else None
            if gau_depth is not None and gau_date_txt:
                # Accept common formats; be permissive (numeric + month-name variants)
                for fmt in (
                    "%m/%d/%Y",
                    "%Y-%m-%d",
                    "%m-%d-%Y",
                    "%Y/%m/%d",
                    "%d %B %Y",      # 31 March 2025
                    "%B %d, %Y",     # March 31, 2025
                    "%d %b %Y",      # 31 Mar 2025
                    "%b %d, %Y",     # Mar 31, 2025
                ):
                    try:
                        gau_dt = _dt.datetime.strptime(str(gau_date_txt), fmt)
                        break
                    except Exception:
                        gau_dt = None
                if gau_dt:
                    age_days = (_dt.datetime.utcnow() - gau_dt).days
                    uqw_age_days = int(age_days)
                    if age_days <= (5 * 365):
                        uqw_depth = gau_depth
                        uqw_source = "gau"
        except Exception:
            uqw_depth = uqw_depth

        # GAU protect intervals: parse recommendation text for surface and zone ranges
        gau_protect_intervals: list[dict] = []
        try:
            import re as _re
            rec = str((gau.get("recommendation") or "") if gau else "")
            # surface to X ft
            m_surf = _re.search(r"surface\s+to\s+a\s+depth\s+of\s+(\d{2,5})\s*feet", rec, flags=_re.IGNORECASE)
            if m_surf:
                top = float(m_surf.group(1)); gau_protect_intervals.append({"top_ft": top, "bottom_ft": 0.0, "source": "gau"})
            # zone from A to B ft
            for m in _re.finditer(r"from\s+a\s+depth\s+of\s+(\d{2,5})\s*feet\s+to\s+(\d{2,5})\s*feet", rec, flags=_re.IGNORECASE):
                a = float(m.group(1)); b = float(m.group(2));
                lo, hi = min(a, b), max(a, b)
                gau_protect_intervals.append({"top_ft": hi, "bottom_ft": lo, "source": "gau"})
        except Exception:
            gau_protect_intervals = gau_protect_intervals

        surface_shoe_ft = None
        surface_size_in = None
        prod_size_in = None
        production_shoe_ft = None
        intermediate_shoe_ft = None
        # Prefer normalized keys from extractor: string,size_in,top_ft,bottom_ft,shoe_depth_ft
        deepest_shoe_any_ft = None
        for row in (w2.get("casing_record") or []):
            kind = (row.get("string") or row.get("type_of_casing") or "").lower()
            if kind.startswith("surface"):
                surface_shoe_ft = row.get("shoe_depth_ft") or row.get("setting_depth_ft") or row.get("bottom_ft")
                surface_size_in = row.get("size_in") or row.get("casing_size_in")
            if kind.startswith("production") and production_shoe_ft is None:
                production_shoe_ft = row.get("shoe_depth_ft") or row.get("setting_depth_ft") or row.get("bottom_ft")
            if kind.startswith("intermediate") and intermediate_shoe_ft is None:
                # Prefer setting depth/bottom of the intermediate string; ignore mis-extracted shoe_depth_ft
                intermediate_shoe_ft = row.get("setting_depth_ft") or row.get("bottom_ft") or None
            # Track deepest shoe/setting/bottom across all strings as fallback for production shoe
            try:
                cand = row.get("shoe_depth_ft") or row.get("setting_depth_ft") or row.get("bottom_ft")
                if cand is not None:
                    cval = float(cand)
                    if (deepest_shoe_any_ft is None) or (cval > float(deepest_shoe_any_ft)):
                        deepest_shoe_any_ft = cval
            except Exception:
                pass
        # Determine production casing size (smallest OD/size in record as proxy)
        sizes = []
        for row in (w2.get("casing_record") or []):
            s = row.get("size_in") or row.get("casing_size_in")
            if s:
                sizes.append(s)
        def _parse_size(txt: str | float | int) -> float | None:
            if txt is None:
                return None
            if isinstance(txt, (int, float)):
                return float(txt)
            t = str(txt).strip().replace('"','')
            # handle forms like "5 1/2", "2 7/8", "8.625"
            if ' ' in t:
                parts = t.split()
                try:
                    whole = float(parts[0])
                except Exception:
                    return None
                frac = 0.0
                if len(parts) > 1 and '/' in parts[1]:
                    num, den = parts[1].split('/')
                    try:
                        frac = float(num) / float(den)
                    except Exception:
                        frac = 0.0
                return whole + frac
            try:
                return float(t)
            except Exception:
                return None
        parsed_sizes = [ps for ps in (_parse_size(s) for s in sizes) if ps]
        if parsed_sizes:
            prod_size_in = min(parsed_sizes)

        # Producing interval from W-2 if present
        prod_iv = None
        try:
            piv = w2.get("producing_injection_disposal_interval") or {}
            if isinstance(piv, dict) and piv.get("from_ft") and piv.get("to_ft"):
                f = float(piv["from_ft"]) if piv["from_ft"] is not None else None
                t = float(piv["to_ft"]) if piv["to_ft"] is not None else None
                if f is not None and t is not None:
                    prod_iv = [f, t]
        except Exception:
            prod_iv = None

        # Formation tops map for overlay formation_tops alignment
        formation_tops_map = {}
        try:
            for rec in (w2.get("formation_record") or []):
                name = str(rec.get("formation") or "").strip().lower()
                top = rec.get("top_ft")
                if name and top is not None:
                    formation_tops_map[name] = float(top)
        except Exception:
            formation_tops_map = {}

        facts = {
            "api14": wrap(api14),
            "state": wrap("TX"),
            "district": wrap(district),
            "county": wrap(county),
            "field": wrap(field),
            "lease": wrap(lease),
            "well_no": wrap(well_no),
            "has_uqw": wrap(bool(gau or uqw_depth)),
            "uqw_base_ft": wrap(uqw_depth),
            # Do not request CIBP by default; only when present/required
            "use_cibp": wrap(False),
            "surface_shoe_ft": wrap(surface_shoe_ft),
        }
        if production_shoe_ft is None and deepest_shoe_any_ft is not None:
            production_shoe_ft = deepest_shoe_any_ft
        if production_shoe_ft is not None:
            try:
                facts["production_shoe_ft"] = wrap(float(production_shoe_ft))
            except Exception:
                pass
        if gau_protect_intervals:
            facts["gau_protect_intervals"] = gau_protect_intervals
        if intermediate_shoe_ft is not None:
            try:
                if float(intermediate_shoe_ft) >= 1500.0:
                    facts["intermediate_shoe_ft"] = wrap(float(intermediate_shoe_ft))
            except Exception:
                pass
        if prod_iv is not None:
            facts["producing_interval_ft"] = wrap(prod_iv)
        if formation_tops_map:
            facts["formation_tops_map"] = formation_tops_map

        logger.info("plan_from_extractions: facts api=%s district=%s shoe=%s uqw=%s", facts["api14"]["value"], facts["district"]["value"], facts["surface_shoe_ft"]["value"], facts["uqw_base_ft"]["value"])

        policy = get_effective_policy(district=facts["district"]["value"], county=facts["county"]["value"] or None, field=facts["field"]["value"] or None)
        policy["policy_id"] = "tx.w3a"
        policy["complete"] = True
        prefs = policy.setdefault("preferences", {})
        prefs["rounding_policy"] = "nearest"
        # Ensure a default recipe exists for materials computation (used by overrides like squeeze)
        prefs.setdefault("default_recipe", {
            "id": "class_h_neat_15_8",
            "class": "H",
            "density_ppg": 15.8,
            "yield_ft3_per_sk": 1.18,
            "water_gal_per_sk": 5.2,
            "additives": [],
        })
        # Enable optional long-plug merge for testing; default threshold 500 ft
        prefs.setdefault("long_plug_merge", {
            "enabled": True,
            "threshold_ft": 500,
            # Allow cross-type merges: formation plugs, GAU protect plugs, UQW isolation
            "types": ["formation_top_plug", "cement_plug", "uqw_isolation_plug"],
            "preserve_tagging": True,
        })

        # Map OD to nominal ID for common casing sizes (inches)
        NOMINAL_ID = {
            11.75: 10.965,
            10.625: 10.2,
            8.625: 7.921,
            7.0: 6.094,
            5.5: 4.778,
        }
        def _nominal_id(size_txt: Any) -> float | None:
            val = _parse_size(size_txt)
            if not val:
                return None
            # direct match
            if val in NOMINAL_ID:
                return NOMINAL_ID[val]
            # try rounding to nearest common
            for k in NOMINAL_ID.keys():
                if abs(val - k) < 0.02:
                    return NOMINAL_ID[k]
            return None

        surface_id = _nominal_id(surface_size_in)
        prod_id = _nominal_id(prod_size_in)
        # Tubing/stinger from W-2 tubing_record
        stinger_od_in = None
        tr = (w2.get("tubing_record") or [])
        if tr:
            stinger_od_in = _parse_size(tr[0].get("size_in"))

        # Populate geometry defaults so materials compute
        gdefs = policy.setdefault("preferences", {}).setdefault("geometry_defaults", {})
        if surface_id and stinger_od_in:
            gdefs.setdefault("surface_casing_shoe_plug", {}).update({
                "casing_id_in": surface_id,
                "stinger_od_in": stinger_od_in,
                "annular_excess": 0.4,
            })
            # Provide surface-cased context defaults for merged near-surface plugs
            gdefs.setdefault("cased_surface", {}).update({
                "casing_id_in": surface_id,
                "stinger_od_in": stinger_od_in,
                "annular_excess": 0.4,
            })
            gdefs.setdefault("uqw_isolation_plug", {}).update({
                "casing_id_in": surface_id,
                "stinger_od_in": stinger_od_in,
                "annular_excess": 0.5,
            })
        # CIBP cap should use production casing ID (where the cap is placed above the plug)
        cap_id = prod_id or surface_id
        if cap_id and stinger_od_in:
            gdefs.setdefault("cibp_cap", {}).update({
                "casing_id_in": cap_id,
                "stinger_od_in": stinger_od_in,
                "annular_excess": 0.4,
            })
        # Squeeze defaults
        if prod_id and stinger_od_in:
            gdefs.setdefault("squeeze", {}).update({
                "casing_id_in": prod_id,
                "stinger_od_in": stinger_od_in,
                "interval_ft": 100.0,
                "squeeze_factor": 0.4,
                "annular_excess": 0.4,
            })
        if prod_id and stinger_od_in:
            gdefs.setdefault("cement_plug", {}).update({
                "casing_id_in": prod_id,
                "stinger_od_in": stinger_od_in,
                "annular_excess": 0.4,
            })
        # Use production casing geometry for formation-top plugs as default cased-annulus
        if prod_id and stinger_od_in:
            gdefs.setdefault("formation_top_plug", {}).update({
                "casing_id_in": prod_id,
                "stinger_od_in": stinger_od_in,
                "annular_excess": 0.4,
            })

        # --- Inject steps_overrides from extracted packet to complete the plan ---
        eff = policy.setdefault("effective", {})
        # 1) perf_circulate from producing interval (W-2)
        try:
            pid = (w2.get("producing_injection_disposal_interval") or None)
            top = bot = None
            if isinstance(pid, dict):
                top = pid.get("from_ft")
                bot = pid.get("to_ft")
            elif isinstance(pid, list) and pid:
                cand = pid[0] if isinstance(pid[0], dict) else None
                if cand:
                    top = cand.get("from_ft")
                    bot = cand.get("to_ft")
            if top is not None and bot is not None:
                t = float(top)
                b = float(bot)
                if abs(b - t) > 0:
                    facts["producing_interval_ft"] = wrap([min(t, b), max(t, b)])
                    eff.setdefault("steps_overrides", {})["perf_circulate"] = [{
                        "top_ft": t,
                        "bottom_ft": b,
                        "citations": ["SWR-14"],
                    }]
                    logger.info("plan_from_extractions: override perf_circulate [%s,%s]", t, b)
        except Exception:
            pass
        # 2) squeeze via perf from W-15 or W-2 operations if present
        try:
            # Prefer W-15 cementing_to_squeeze interval when available
            t_best = None
            b_best = None
            sacks_override = None
            cts_db = w15.get("cementing_to_squeeze") or []
            cts = cts_db
            # Also read tmp extraction and choose the widest span across DB and tmp
            cts_tmp: list = []
            if api:
                try:
                    tmp_path = os.path.join("ra_config", "tmp", "extractions", f"W-15_{api}_w15.json")
                    if os.path.exists(tmp_path):
                        with open(tmp_path, "r", encoding="utf-8") as f:
                            w15_tmp = json.load(f) or {}
                        cts_tmp = w15_tmp.get("cementing_to_squeeze") or []
                except Exception:
                    cts_tmp = []
            # Pick source with widest continuous span
            def _span(rows: list) -> float:
                try:
                    tops = [float(r.get("top_ft")) for r in rows if r.get("top_ft") is not None]
                    bots = [float(r.get("bottom_ft")) for r in rows if r.get("bottom_ft") is not None]
                    return (max(bots) - min(tops)) if (tops and bots) else 0.0
                except Exception:
                    return 0.0
            if _span(cts_tmp) > _span(cts_db):
                cts = cts_tmp
            if isinstance(cts, list) and cts:
                # Always use the full W-15 span across all rows
                try:
                    tops = [float(r.get("top_ft")) for r in cts if r.get("top_ft") is not None]
                    bots = [float(r.get("bottom_ft")) for r in cts if r.get("bottom_ft") is not None]
                    if tops and bots:
                        t_best, b_best = min(tops), max(bots)
                    # Prefer sacks from any W-15 method mentioning SXS
                    try:
                        import re as _re
                        for r in cts:
                            method_txt = str(r.get("method") or "")
                            m_sx = _re.search(r"(\d{2,5})\s*SXS", method_txt, flags=_re.IGNORECASE)
                            if m_sx:
                                sacks_override = int(m_sx.group(1))
                                break
                    except Exception:
                        sacks_override = sacks_override
                except Exception:
                    t_best = b_best = None
            # Fallback to W-2 ops / remarks
            if t_best is None or b_best is None:
                raw_ops = w2.get("acid_fracture_operations") or []
                ops = []
                if isinstance(raw_ops, dict):
                    ops = raw_ops.get("operations") or []
                elif isinstance(raw_ops, list):
                    ops = raw_ops
                sqz = next((o for o in ops if (str(o.get("type_of_operation") or "").lower().find("squeeze") >= 0)), None)
                if sqz:
                    di = sqz.get("depth_interval_ft")
                    if isinstance(di, list) and len(di) == 2:
                        t_best, b_best = float(di[0]), float(di[1])
                    else:
                        t_val = sqz.get("depth_interval_ft_from")
                        b_val = sqz.get("depth_interval_ft_to")
                        if t_val is not None and b_val is not None:
                            t_best, b_best = float(t_val), float(b_val)
                # Heuristic from W-2 remarks: use reset depth as top and squeeze-at depth as bottom
                if (t_best is None or b_best is None):
                    import re as _re
                    txt = (w2.get("remarks") or "") + " " + (w2.get("rrc_remarks") or "")
                    m_bot = _re.search(r"squeez\w*[^\d]{0,20}(\d{3,5})", txt, flags=_re.IGNORECASE)
                    m_top = _re.search(r"re[- ]?set[^\d]{0,20}(\d{3,5})", txt, flags=_re.IGNORECASE)
                    if m_bot and m_top:
                        try:
                            bottom_cand = float(m_bot.group(1))
                            top_cand = float(m_top.group(1))
                            if top_cand > bottom_cand:
                                t_best, b_best = bottom_cand, top_cand
                        except Exception:
                            pass
            if t_best is not None and b_best is not None and abs(b_best - t_best) > 0:
                t_f, b_f = min(t_best, b_best), max(t_best, b_best)
                # Force final interval to W-15 span when available: prefer SXS row with the highest top_ft
                try:
                    if isinstance(cts, list) and cts:
                        sxs_rows = [r for r in cts if isinstance(r, dict) and str(r.get("method") or "").lower().find("sxs") >= 0]
                        row = None
                        if sxs_rows:
                            row = max(sxs_rows, key=lambda r: float(r.get("top_ft") or -1e9))
                        else:
                            row = max(cts, key=lambda r: float(r.get("top_ft") or -1e9))
                        if row and row.get("top_ft") is not None and row.get("bottom_ft") is not None:
                            t_row = float(row.get("top_ft")); b_row = float(row.get("bottom_ft"))
                            t_f, b_f = min(t_row, b_row), max(t_row, b_row)
                except Exception:
                    pass
                # Try to parse sacks from W-2 remarks/rrc_remarks (e.g., "830 SXS")
                try:
                    import re as _re
                    text = (w2.get("remarks") or "") + " " + (w2.get("rrc_remarks") or "")
                    m = _re.search(r"(\d{2,5})\s*SXS", text, flags=_re.IGNORECASE)
                    if m:
                        sacks_override = int(m.group(1))
                    # If still not found and we had W-15 rows, try parsing sacks from their method fields
                    if (sacks_override is None) and isinstance(cts, list):
                        for r in cts:
                            mt = str(r.get("method") or "")
                            m2 = _re.search(r"(\d{2,5})\s*SXS", mt, flags=_re.IGNORECASE)
                            if m2:
                                sacks_override = int(m2.group(1))
                                break
                except Exception:
                    sacks_override = None
                # Force interval to W-15 if present in cementing_to_squeeze (full span)
                if isinstance(cts, list) and cts:
                    try:
                        tops = [float(r.get("top_ft")) for r in cts if r.get("top_ft") is not None]
                        bots = [float(r.get("bottom_ft")) for r in cts if r.get("bottom_ft") is not None]
                        if tops and bots:
                            t_f, b_f = min(tops), max(bots)
                    except Exception:
                        pass
                ov = {
                    "interval_ft": [t_f, b_f],
                    "citations": ["District overlay: cap-above-perf"],
                }
                # Mark as performed (historical) if sourced from W-15 or W-2 remarks
                ov["performed"] = True
                if cts:
                    ov.setdefault("citations", []).append("W-15: cementing_report")
                else:
                    ov.setdefault("citations", []).append("W-2: remarks")
                # Safety: if DB W-15 rows exist and produce a wider span than current, prefer that
                try:
                    cts_db_check = (w15.get("cementing_to_squeeze") or [])
                    db_tops = [float(r.get("top_ft")) for r in cts_db_check if r.get("top_ft") is not None]
                    db_bots = [float(r.get("bottom_ft")) for r in cts_db_check if r.get("bottom_ft") is not None]
                    if db_tops and db_bots:
                        db_lo, db_hi = min(db_tops), max(db_bots)
                        cur_lo, cur_hi = float(ov["interval_ft"][0]), float(ov["interval_ft"][1])
                        if (db_hi - db_lo) > (cur_hi - cur_lo):
                            ov["interval_ft"] = [db_lo, db_hi]
                except Exception:
                    pass
                if sacks_override is not None:
                    ov["sacks_override"] = sacks_override
                eff.setdefault("steps_overrides", {})["squeeze_via_perf"] = ov
                logger.info("plan_from_extractions: override squeeze interval [%s,%s]", t_f, b_f)
        except Exception:
            pass
        # 3) Mud instruction from packet remarks
        try:
            remarks = (w2.get("remarks") or "") + " " + ((w2.get("operator_certification") or {}).get("title") or "")
            if "9.5" in remarks and ("ppg" in remarks.lower() or "mud" in remarks.lower()):
                eff.setdefault("preferences", {}).setdefault("operational", {})["mud_min_weight_ppg"] = 9.5
                logger.info("plan_from_extractions: operational mud_min_weight_ppg=9.5")
        except Exception:
            pass

        # 4a) Mechanical barrier awareness from W-2 remarks (existing CIBP / Packer / DV Tool)
        try:
            import re as _re
            txt = f"{w2.get('remarks') or ''} {w2.get('rrc_remarks') or ''}"
            mech: list[str] = []
            # CIBP depth
            cibp_ft = None
            for pat in [r"CIBP\s*(?:at)?\s*(\d{3,5})", r"cast\s*iron\s*bridge\s*plug\s*(?:at)?\s*(\d{3,5})", r"\bBP\b\s*(?:at)?\s*(\d{3,5})"]:
                m = _re.search(pat, txt, flags=_re.IGNORECASE)
                if m:
                    try:
                        cibp_ft = float(m.group(1))
                        mech.append("CIBP")
                        break
                    except Exception:
                        pass
            # Packer depth
            packer_ft = None
            m_p = _re.search(r"packer\s*(?:at|set\s*at)?\s*(\d{3,5})", txt, flags=_re.IGNORECASE)
            if m_p:
                try:
                    packer_ft = float(m_p.group(1))
                    if "PACKER" not in mech:
                        mech.append("PACKER")
                except Exception:
                    pass
            # DV tool depth
            dv_ft = None
            for pat in [r"DV[- ]?(?:stage)?\s*tool\s*(?:at)?\s*(\d{3,5})", r"DV[- ]?tool\s*(\d{3,5})"]:
                m = _re.search(pat, txt, flags=_re.IGNORECASE)
                if m:
                    try:
                        dv_ft = float(m.group(1))
                        break
                    except Exception:
                        pass
            if dv_ft is not None and "DV_TOOL" not in mech:
                mech.append("DV_TOOL")

            if mech:
                facts["existing_mechanical_barriers"] = mech
            if cibp_ft is not None:
                facts["existing_cibp_ft"] = wrap(cibp_ft)
                facts["cibp_present"] = wrap(True)
            if packer_ft is not None:
                facts["packer_ft"] = wrap(packer_ft)
            if dv_ft is not None:
                facts["dv_tool_ft"] = wrap(dv_ft)
        except Exception:
            pass

        # 4) Do not scrape proposal intervals from remarks; kernel generates proposal from overlays

        out = plan_from_facts(facts, policy)

        # Print a concise JSON summary (steps and sacks)
        import json as _json
        def _step_summary(s: Dict[str, Any]) -> Dict[str, Any]:
            out = {
                "type": s.get("type"),
                "top_ft": s.get("top_ft"),
                "bottom_ft": s.get("bottom_ft"),
                # surface computed sacks if present
                "sacks": ((s.get("materials") or {}).get("slurry") or {}).get("sacks") or s.get("sacks"),
                "regulatory_basis": s.get("regulatory_basis"),
                "special_instructions": s.get("special_instructions"),
                "details": (s.get("details") or {}),
            }
            # add cement class annotation and mid-depth if present
            if s.get("type") == "cement_plug":
                out["cement_class"] = s.get("cement_class")
                out["depth_mid_ft"] = s.get("depth_mid_ft")
            # Include a brief materials explain when present
            try:
                m = ((s.get("materials") or {}).get("slurry") or {})
                if isinstance(m.get("explain"), dict):
                    out.setdefault("details", {})["materials_explain"] = m.get("explain")
            except Exception:
                pass
            # Add explicit sacks_override provenance when squeeze shows fixed sacks
            try:
                if s.get("type") == "squeeze" and isinstance(out.get("details", {}).get("materials_explain"), dict):
                    out["details"]["materials_explain"].setdefault("sacks_override_from", "W-15 cementing report")
            except Exception:
                pass
            return out

        # Derive county/field and formation sets for visibility
        county_val = facts.get("county", {}).get("value") if isinstance(facts.get("county"), dict) else facts.get("county")
        field_val = facts.get("field", {}).get("value") if isinstance(facts.get("field"), dict) else facts.get("field")
        tops_map = facts.get("formation_tops_map") or {}
        detected_formations = sorted(list(tops_map.keys())) if isinstance(tops_map, dict) else []
        targeted_formations: list[str] = []
        try:
            for s in out.get("steps", []) or []:
                fm = s.get("formation")
                if isinstance(fm, str):
                    targeted_formations.append(fm)
                bases = s.get("regulatory_basis") or []
                if isinstance(bases, list):
                    for b in bases:
                        if isinstance(b, str) and ":formation_top:" in b:
                            targeted_formations.append(b.split(":formation_top:", 1)[1])
                        if isinstance(b, str) and ":mid." in b:
                            targeted_formations.append(b.split(":mid.", 1)[1])
        except Exception:
            pass
        targeted_formations = sorted(list({str(x) for x in targeted_formations if x}))

        # Plan notes for quick review
        plan_notes = []
        try:
            if facts.get("existing_cibp_ft", {}).get("value"):
                plan_notes.append(f"Existing CIBP at {int(float(facts['existing_cibp_ft']['value']))} ft – tag and cap only; do not drill out.")
            if facts.get("dv_tool_ft", {}).get("value"):
                plan_notes.append(f"DV tool isolation considered at {int(float(facts['dv_tool_ft']['value']))} ft.")
            sqz_ov = (policy.get("effective") or {}).get("steps_overrides", {}).get("squeeze_via_perf") or {}
            if isinstance(sqz_ov.get("interval_ft"), list) and len(sqz_ov["interval_ft"]) == 2:
                t_s, b_s = sqz_ov["interval_ft"][0], sqz_ov["interval_ft"][1]
                sxs = sqz_ov.get("sacks_override")
                if sxs:
                    plan_notes.append(f"Squeeze interval {int(t_s)}–{int(b_s)} ft per W-15, {int(sxs)} sks applied.")
        except Exception:
            pass

        # Include debug of overrides for inspection
        squeeze_debug = None
        try:
            squeeze_debug = ((policy.get("effective") or {}).get("steps_overrides") or {}).get("squeeze_via_perf")
        except Exception:
            squeeze_debug = None

        # plan-level totals for materials
        total_sacks = 0
        total_bbl = 0.0
        try:
            for s in out.get("steps", []):
                sl = ((s.get("materials") or {}).get("slurry") or {})
                if isinstance(sl.get("sacks"), (int, float)):
                    total_sacks += int(sl.get("sacks"))
                if isinstance(sl.get("total_bbl"), (int, float)):
                    total_bbl += float(sl.get("total_bbl"))
        except Exception:
            pass

        result = {
            "api": api,
            "jurisdiction": out.get("jurisdiction"),
            "district": out.get("district"),
            "county": county_val,
            "field": field_val,
            "field_resolution": policy.get("field_resolution"),
            "formation_tops_detected": detected_formations,
            "formations_targeted": targeted_formations,
            "rounding": (out.get("materials_policy") or {}).get("rounding"),
            "steps": [_step_summary(s) for s in out.get("steps", [])],
            "plan_notes": plan_notes or None,
            "materials_totals": {
                "total_sacks": total_sacks if total_sacks > 0 else None,
                "total_bbl": round(total_bbl, 2) if total_bbl > 0 else None,
            },
            "debug_overrides": {
                "squeeze_via_perf": squeeze_debug
            },
            "rrc_export": [
                (
                    {
                        "plug_no": idx + 1,
                        "type": (
                            "CIBP" if (s.get("type") == "bridge_plug") else (
                                "CIBP cap" if (s.get("type") in ("bridge_plug_cap", "cibp_cap")) else s.get("type")
                            )
                        ),
                        "from_ft": (s.get("bottom_ft") if s.get("bottom_ft") is not None else s.get("depth_ft")),
                        "to_ft": (s.get("top_ft") if s.get("top_ft") is not None else s.get("depth_ft")),
                        "sacks": ((s.get("materials") or {}).get("slurry") or {}).get("sacks"),
                        "remarks": ", ".join(filter(None, [
                            ("; ".join(s.get("regulatory_basis") or []) if isinstance(s.get("regulatory_basis"), list) else None),
                            (s.get("placement_basis") or (s.get("details") or {}).get("placement_basis")),
                        ])) or None,
                    }
                )
                for idx, s in enumerate(
                    sorted(
                        out.get("steps", []),
                        key=lambda x: (
                            float(
                                (x or {}).get("bottom_ft")
                                if (x or {}).get("bottom_ft") is not None
                                else (x or {}).get("depth_ft")
                                or 0.0
                            )
                        ),
                        reverse=True,
                    )
                )
            ],
            "violations": out.get("violations", []),
        }
        if gau_protect_intervals:
            result["gau_protect_intervals"] = gau_protect_intervals
        # Annotate UQW provenance on the corresponding step for transparency
        try:
            if uqw_source or uqw_age_days is not None or uqw_depth is not None:
                for s in result["steps"]:
                    if s.get("type") == "uqw_isolation_plug":
                        d = s.setdefault("details", {})
                        d["uqw_base_source"] = uqw_source or "none"
                        if uqw_age_days is not None:
                            d["uqw_base_age_days"] = int(uqw_age_days)
                        if uqw_depth is not None:
                            d["uqw_base_ft"] = float(uqw_depth)
                        break
        except Exception:
            pass
        self.stdout.write(_json.dumps(result, indent=2))

        # --- Compose an additional RRC W-3A friendly view ---
        try:
            def _fmt_api14(a: str) -> str:
                if not a:
                    return ""
                # Convert 14-digit to 42-xxx-xxxxx form when possible
                s = a.replace("-", "")
                if len(s) >= 10:
                    return f"{s[0:2]}-{s[2:5]}-{s[5:]}"
                return a

            # Section A
            w3a_section_a = {
                "api": _fmt_api14(api14),
                "district": district,
                "county": county,
                "field": field,
                "lease": lease,
                "well_no": well_no,
                # Operator not always present in extractions; omit if unknown
                "operator": (w2.get("well_info") or {}).get("operator_name") or None,
            }

            # Section B: build plugs table deepest → shallowest
            def _plug_name(s: Dict[str, Any]) -> str:
                t = str(s.get("type") or "").replace("_", " ").strip()
                t = t.title()
                if s.get("type") == "formation_top_plug":
                    fm = s.get("formation") or (s.get("details") or {}).get("center_formation")
                    if fm:
                        return f"Formation Top Plug ({fm})"
                if s.get("type") == "cement_plug" and (s.get("placement_basis") or "").lower().find("gau") >= 0:
                    return "GAU Protect Interval Plug"
                if s.get("type") == "uqw_isolation_plug":
                    return "UQW Isolation Plug"
                return t

            def _materials_method(s: Dict[str, Any]) -> str | None:
                cls = s.get("cement_class") or ((s.get("details") or {}).get("cement_class")) or "C"
                tag = " tagged" if s.get("tag_required") or ((s.get("details") or {}).get("verification", {}).get("action") == "TAG") else ""
                wait = " 4 hr wait" if ((s.get("details") or {}).get("verification", {}).get("required_wait_hr") == 4) else ""
                base = f"Class {cls} cement{tag}{wait}".strip()
                return base or None

            steps_sorted = sorted(out.get("steps", []), key=lambda s: (float(s.get("bottom_ft") or 0.0)), reverse=True)
            w3a_rows = []
            for idx, s in enumerate(steps_sorted, start=1):
                try:
                    row = {
                        "plug_no": idx,
                        "from_ft": s.get("top_ft"),
                        "to_ft": s.get("bottom_ft"),
                        "type": _plug_name(s),
                        "sacks": ((s.get("materials") or {}).get("slurry") or {}).get("sacks") or s.get("sacks"),
                        "materials_method": _materials_method(s),
                        "remarks": "; ".join((s.get("regulatory_basis") or [])) or None,
                    }
                    w3a_rows.append(row)
                except Exception:
                    continue

            # Section C: materials and notes
            w3a_section_c = {
                "materials_summary": {
                    "total_sacks": result["materials_totals"].get("total_sacks"),
                    "total_barrels": result["materials_totals"].get("total_bbl"),
                    "cement_classes_used": ["C", "H"],
                },
                "plan_notes": result.get("plan_notes") or [],
            }

            result_w3a = {
                "section_a": w3a_section_a,
                "section_b_plugs": w3a_rows,
                "section_c": w3a_section_c,
            }

            # Save alongside main JSON for convenience
            try:
                out_dir = os.path.join("ra_config", "tmp", "extractions")
                os.makedirs(out_dir, exist_ok=True)
                out_path = os.path.join(out_dir, f"W3A_{api}_plan_w3a.json")
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(_json.dumps(result_w3a, indent=2))
            except Exception:
                pass
        except Exception:
            # Do not fail command if formatting view fails
            pass



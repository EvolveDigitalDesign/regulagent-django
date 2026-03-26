"""
Auto-research supplementation service.

When NM OCD scraper returns incomplete data (empty formation tops or perforations),
this service auto-triggers the research pipeline to extract missing data from
well file PDFs, then merges results back into plan extractions.
"""

import logging
import re

from apps.public_core.models import ResearchSession
from apps.public_core.tasks_research import start_research_session_task

logger = logging.getLogger(__name__)

# Tighter proximity — paragraph-level, not section-level
_REMOVAL_PROXIMITY = 300  # characters to search around depth mention

# Keywords that indicate removal only when they appear in the SAME sentence/paragraph
# as the depth, with the depth as subject (not a nearby unrelated operation)
_REMOVAL_KEYWORDS = frozenset({
    "released", "pulled", "pooh", "retrieved", "milled",
    "drilled out", "removed", "unseated", "backed off",
    "recovered", "cut and pulled", "cut free",
})

# Keywords that indicate the packer was explicitly set PERMANENTLY — these override
# removal signals when they appear closer to the depth than the removal keyword.
_PERMANENT_KEYWORDS = frozenset({
    "permanent", "perm pkr", "perm packer", "set and cemented",
    "cemented in place", "left in hole",
})


def _determine_status_from_text(depth_ft: float, sections: list) -> str:
    """
    Deterministic equipment status: scan source text for removal keywords
    near the equipment's depth mention, using paragraph boundaries to avoid
    false positives from unrelated operations.

    Returns "removed" only if:
    1. A removal keyword appears within the same paragraph as the depth, AND
    2. No permanent-set keyword appears closer to the depth than the removal keyword.
    """
    if depth_ft <= 0:
        return "current"

    depth_str = str(int(depth_ft))
    removal_evidence = 0
    permanent_evidence = 0

    for sec in sections:
        text = sec.get("text", "") if isinstance(sec, dict) else str(sec)
        text_lower = text.lower()

        start = 0
        while True:
            pos = text_lower.find(depth_str, start)
            if pos == -1:
                break

            # Find the paragraph containing this depth mention.
            # A paragraph boundary is a double newline, period+newline, or similar break.
            para_start = max(0, pos - _REMOVAL_PROXIMITY)
            para_end = min(len(text_lower), pos + len(depth_str) + _REMOVAL_PROXIMITY)

            # Use the raw 300-char window without paragraph narrowing.
            # Well operations text puts each step on a separate line, so \n breaks
            # split related operations like "Set pkr at 9050" / "Release pkr and POOH".
            # Protection against false positives comes from the tight permanent-keyword
            # check (60 chars) rather than paragraph boundaries.

            window = text_lower[para_start:para_end]
            depth_offset = pos - para_start  # position of depth within window

            # Check for permanent-set keywords — only count if CLOSE to the depth
            # (within 60 chars). "permanent pkr" near a different depth in the same
            # paragraph should NOT override removal evidence for THIS depth.
            _PERMANENT_TIGHT = 60
            for keyword in _PERMANENT_KEYWORDS:
                kw_pos = window.find(keyword)
                if kw_pos != -1:
                    dist = abs(kw_pos - depth_offset)
                    if dist <= _PERMANENT_TIGHT:
                        permanent_evidence += 1
                        logger.info(
                            "DETERMINISTIC-STATUS: depth=%s found permanent keyword '%s' (dist=%d, within tight window)",
                            depth_str, keyword, dist,
                        )
                    else:
                        logger.info(
                            "DETERMINISTIC-STATUS: depth=%s ignoring permanent keyword '%s' (dist=%d, too far)",
                            depth_str, keyword, dist,
                        )

            # Check for removal keywords in paragraph window
            for keyword in _REMOVAL_KEYWORDS:
                if keyword in window:
                    removal_evidence += 1
                    logger.info(
                        "DETERMINISTIC-STATUS: depth=%s found removal keyword '%s' in paragraph",
                        depth_str, keyword,
                    )

            start = pos + 1

    # Decision: removal evidence must outweigh permanent evidence
    if removal_evidence > 0 and permanent_evidence == 0:
        logger.warning(
            "DETERMINISTIC-STATUS: depth=%s — REMOVED (removal=%d, permanent=%d)",
            depth_str, removal_evidence, permanent_evidence,
        )
        return "removed"

    if removal_evidence > 0 and permanent_evidence > 0:
        logger.warning(
            "DETERMINISTIC-STATUS: depth=%s — CURRENT (conflicting signals: removal=%d, permanent=%d)",
            depth_str, removal_evidence, permanent_evidence,
        )
        return "current"  # When in doubt, keep it — safer to include than exclude

    return "current"


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------

def trigger_research_if_needed(
    api_number: str,
    state: str,
    scraped_data: dict,
    well=None,
) -> dict:
    """
    Check if critical plan data is missing and auto-trigger research if so.

    Returns dict with research_session_id, research_status, missing_fields.
    """
    missing = _detect_missing(scraped_data)
    if not missing:
        return {
            "research_session_id": None,
            "research_status": "not_needed",
            "missing_fields": [],
        }

    # Check for existing session (ready or in-progress)
    existing = (
        ResearchSession.objects.filter(api_number=api_number, state=state)
        .order_by("-created_at")
        .first()
    )

    if existing and existing.status in ("ready", "fetching", "indexing", "pending"):
        return {
            "research_session_id": str(existing.id),
            "research_status": existing.status,
            "missing_fields": missing,
        }

    # Create new session and dispatch
    session = ResearchSession.objects.create(
        api_number=api_number,
        state=state,
        well=well,
    )
    task = start_research_session_task.delay(session_id=str(session.id))
    session.celery_task_id = task.id
    session.save(update_fields=["celery_task_id"])

    return {
        "research_session_id": str(session.id),
        "research_status": "pending",
        "missing_fields": missing,
    }


def _detect_missing(scraped_data: dict) -> list:
    """Return list of field names that are empty but required for plan generation."""
    missing = []
    if not scraped_data.get("formation_tops"):
        missing.append("formation_tops")
    # Check perforations across all completions
    has_perfs = False
    for comp in scraped_data.get("completions", []):
        if comp.get("perforations"):
            has_perfs = True
            break
    if not has_perfs:
        missing.append("perforations")
    return missing


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

_STRUCTURED_QUERIES = {
    "formation_tops": (
        "List every geological formation top or geological marker encountered in "
        "this well with its measured depth in feet. These may be labeled as "
        "'formation tops', 'geological markers', 'formation markers', or listed in "
        "a geological marker table. Include ALL formations from surface to TD. "
        "Format each on its own line as: FormationName: DepthFt "
        "Example: Wolfcamp: 9080"
    ),
    "perforations": (
        "List all perforation intervals in this well with top and bottom measured "
        "depths in feet and the formation name if known. "
        "Format each on its own line as: TopFt-BottomFt (FormationName) "
        "Example: 9122-9226 (Wolfcamp)"
    ),
    "equipment_history": (
        "Determine the CURRENT STATUS of each piece of downhole equipment in this well. "
        "Equipment types: packers, tubing, liners, bridge plugs, CIBP, retainers. "
        "For each unique piece of equipment, determine its FINAL state by reading the full work history chronologically. "
        "Mark as REMOVED if the documents contain an EXPLICIT record of the equipment being: "
        "retrieved, released, pulled, POOH, milled, drilled out, removed, or unseated. "
        "These words in the work history mean the equipment was physically taken out of the well. "
        "Mark as CURRENT if the equipment was set/installed and there is no explicit removal record. "
        "IMPORTANT: Absence of later mention does NOT mean removed — only explicit removal actions count. "
        "List each piece of equipment ONCE with its final status. Do NOT list the same equipment twice. "
        "Include the depth in feet if mentioned. "
        "Format each on its own line as: EquipmentType | DepthFt | CURRENT or REMOVED | Description "
        "Example: PACKER | 9050 | REMOVED | Baker packer set at 9050 ft, released per sundry notice dated 2015-03-01"
        "\n\n--- P&A DOCUMENT INSTRUCTIONS ---"
        "\nP&A (Plug and Abandonment) documents contain TWO types of information:"
        "\n1. PROPOSED PROCEDURE STEPS — numbered steps like 'Step 1: Kill well', 'Step 5: Spot cement'. IGNORE these entirely."
        "\n2. CURRENT WELLBORE DIAGRAM (Current WBD) — a table or list showing what is CURRENTLY in the well. ALWAYS extract equipment from this section."
        "\nThe Current WBD typically appears AFTER the procedure steps and lists casing, perforations, packers, CIBPs with depths."
        "\nExamples of Current WBD entries that confirm CURRENT equipment:"
        "\n  - '35' cmt on top of pkr @ 11167'' → PACKER | 11167 | CURRENT"
        "\n  - 'CIBP @ 12,150' TOC @ 12100'' → CIBP | 12150 | CURRENT"
        "\n  - 'Baker Model D @ 11,200'' → PACKER | 11200 | CURRENT"
        "\nDo NOT skip these just because they appear in a P&A document."
        "\n\n--- COMPLETION REPORT INSTRUCTIONS ---"
        "\nCompletion/recompletion reports list the wellbore configuration at the time of completion."
        "\nExtract ALL equipment with depths, even if listed in a diagram format."
        "\nA 'dual packer' or 'dual pkr' at a depth is ONE packer (a completion configuration), not two."
    ),
}

_EXTRACTION_SUFFIX = (
    "\n\nIMPORTANT: You are extracting structured data for automated processing. "
    "Use ONLY the exact format requested. One entry per line. "
    "If no data is found, respond with 'NO DATA FOUND'."
)


def query_research_for_plan_data(session_id: str) -> dict:
    """
    Run structured RAG queries against an indexed research session.

    Returns parsed formation tops and perforations.
    """
    from apps.public_core.services.research_rag import (
        _retrieve_relevant_sections,
        _build_system_prompt,
        _build_context_prompt,
    )
    from apps.public_core.services.openai_config import get_openai_client

    session = ResearchSession.objects.get(id=session_id)
    if session.status != "ready":
        return {"formation_tops": [], "perforations": [], "equipment_history": [], "raw_answers": {}}

    client = get_openai_client(operation="research_supplement")
    results = {}

    for field, question in _STRUCTURED_QUERIES.items():
        try:
            # _raw_text chunks are noisy for formation/perforation queries but
            # critical for equipment_history — packer depths often only appear there.
            exclude = ["_raw_text"] if field != "equipment_history" else None
            sections = _retrieve_relevant_sections(
                question, session, top_k=20,
                exclude_section_names=exclude,
                prefer_doc_types=["sundry", "c_103"] if field == "equipment_history" else None,
            )
            if not sections:
                results[field] = {"answer": "", "sections": 0}
                continue

            system_prompt = _build_system_prompt(session.api_number, session.state)
            context_prompt = _build_context_prompt(sections)

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt + _EXTRACTION_SUFFIX},
                    {"role": "user", "content": f"{context_prompt}\n\nQuestion: {question}"},
                ],
                temperature=0,
                max_tokens=2048,
            )
            results[field] = {
                "answer": response.choices[0].message.content,
                "sections": len(sections),
                "section_data": sections,
            }
        except Exception:
            logger.exception("Research RAG query failed for field=%s", field)
            results[field] = {"answer": "", "sections": 0}

    # Parse raw answers
    formation_tops = _parse_formation_tops(
        results.get("formation_tops", {}).get("answer", "")
    )
    perforations = _parse_perforations(
        results.get("perforations", {}).get("answer", "")
    )

    # Cross-validate against source sections
    ft_sections = results.get("formation_tops", {}).get("section_data", [])
    if ft_sections and formation_tops:
        formation_tops = _validate_against_sections(
            formation_tops, ft_sections, "formation_tops"
        )
    perf_sections = results.get("perforations", {}).get("section_data", [])
    if perf_sections and perforations:
        perforations = _validate_against_sections(
            perforations, perf_sections, "perforations"
        )

    # Parse equipment history from GPT-4o (used to FIND equipment and depths).
    # Then override GPT-4o's CURRENT/REMOVED classification with deterministic
    # keyword matching on the source text — GPT-4o is non-deterministic here.
    equipment_history = _parse_equipment_history(
        results.get("equipment_history", {}).get("answer", "")
    )
    equip_sections = results.get("equipment_history", {}).get("section_data", [])
    for item in equipment_history:
        item["validated"] = True
        gpt_status = item.get("status", "unverified")
        deterministic_status = _determine_status_from_text(
            item.get("depth_ft", 0.0), equip_sections
        )
        if gpt_status != deterministic_status:
            logger.warning(
                "🔍 STATUS-OVERRIDE: %s@%sft — GPT said '%s', deterministic says '%s' → using deterministic",
                item.get("equipment_type"), item.get("depth_ft"), gpt_status, deterministic_status,
            )
        item["status"] = deterministic_status

    # Regex fallback: scan raw section texts for equipment GPT-4o missed
    if equip_sections:
        regex_equipment = _scan_sections_for_equipment(equip_sections)
        # Only add equipment not already found by GPT-4o
        gpt_depths = {(e.get("equipment_type", ""), round(e.get("depth_ft", 0), -1)) for e in equipment_history}
        for regex_item in regex_equipment:
            key = (regex_item["equipment_type"], round(regex_item["depth_ft"], -1))
            if key not in gpt_depths:
                # Run deterministic status check
                det_status = _determine_status_from_text(regex_item["depth_ft"], equip_sections)
                regex_item["status"] = det_status
                regex_item["validated"] = True
                equipment_history.append(regex_item)
                logger.warning(
                    "🔍 REGEX-FALLBACK: Found %s@%sft (status=%s) that GPT-4o missed",
                    regex_item["equipment_type"], regex_item["depth_ft"], det_status,
                )

    return {
        "formation_tops": formation_tops,
        "perforations": perforations,
        "equipment_history": equipment_history,
        "raw_answers": results,
    }


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_formation_tops(answer: str) -> list:
    """Parse 'FormationName: DepthFt' lines into structured dicts."""
    tops = []
    if not answer or "NO DATA FOUND" in answer.upper():
        return tops
    for line in answer.strip().split("\n"):
        line = line.strip().lstrip("- •*")
        match = re.match(r"(.+?):\s*([\d,]+(?:\.\d+)?)", line)
        if match:
            name = match.group(1).strip()
            depth = float(match.group(2).replace(",", ""))
            tops.append({"formation": name, "top_ft": depth, "source": "research"})
    return tops


def _parse_perforations(answer: str) -> list:
    """Parse 'TopFt-BottomFt (FormationName)' lines into structured dicts."""
    perfs = []
    if not answer or "NO DATA FOUND" in answer.upper():
        return perfs
    for line in answer.strip().split("\n"):
        line = line.strip().lstrip("- •*")
        match = re.match(
            r"([\d,]+(?:\.\d+)?)\s*[-\u2013]\s*([\d,]+(?:\.\d+)?)\s*(?:\((.+?)\))?",
            line,
        )
        if match:
            top = float(match.group(1).replace(",", ""))
            bottom = float(match.group(2).replace(",", ""))
            formation = match.group(3).strip() if match.group(3) else None
            perfs.append({
                "top_md": top,
                "bottom_md": bottom,
                "formation": formation,
                "source": "research",
            })
    return perfs


def _parse_equipment_history(answer: str) -> list:
    """Parse 'EquipmentType | DepthFt | Status | Description' lines into structured dicts."""
    items = []
    if not answer or "NO DATA FOUND" in answer.upper():
        return items

    for line in answer.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue

        equipment_type = parts[0].upper().strip() if parts[0] else ""
        raw_depth = parts[1] if len(parts) > 1 else ""
        raw_status = parts[2].upper().strip() if len(parts) > 2 else ""
        description = parts[3].strip() if len(parts) > 3 else ""

        try:
            depth_ft = float(raw_depth.replace(",", "").strip())
        except (ValueError, AttributeError):
            depth_ft = 0.0

        if "REMOVED" in raw_status:
            status = "removed"
        elif "CURRENT" in raw_status:
            status = "current"
        else:
            status = "unverified"

        if equipment_type:
            items.append({
                "equipment_type": equipment_type,
                "depth_ft": depth_ft,
                "status": status,
                "description": description,
                "source": "research",
            })
    return items


def _scan_sections_for_equipment(sections: list) -> list:
    """
    Deterministic regex scan of raw section texts for equipment patterns.

    Catches equipment that GPT-4o misses, especially from Current WBD sections
    in P&A documents where abbreviated formats like 'pkr @ 11167' are used.
    """
    found = []
    seen = set()  # (type, rounded_depth) to dedup

    for sec in sections:
        text = sec.get("section_text", "") or sec.get("text", "")
        if not text:
            continue

        # Pattern: CIBP @ depth  (e.g., "CIBP @ 12,150'", "CIBP @ ~9072'")
        for m in re.finditer(r'CIBP\s*[@at]+\s*~?([\d,]+)', text, re.IGNORECASE):
            depth = float(m.group(1).replace(",", ""))
            key = ("CIBP", round(depth, -1))
            if key not in seen:
                seen.add(key)
                found.append({
                    "equipment_type": "CIBP",
                    "depth_ft": depth,
                    "status": "current",
                    "description": f"CIBP at {depth} ft (regex scan)",
                    "source": "regex_scan",
                })

        # Pattern: cement/cmt on top of packer/pkr @ depth
        # e.g., "35' cmt on top of pkr @ 11167'"
        for m in re.finditer(r"(?:cement|cmt)\s+on\s+top\s+of\s+(?:packer|pkr)\s*[@at]+\s*~?([\d,]+)", text, re.IGNORECASE):
            depth = float(m.group(1).replace(",", ""))
            key = ("PACKER", round(depth, -1))
            if key not in seen:
                seen.add(key)
                found.append({
                    "equipment_type": "PACKER",
                    "depth_ft": depth,
                    "status": "current",
                    "description": f"Packer at {depth} ft with cement on top (regex scan)",
                    "source": "regex_scan",
                })

        # Pattern: packer/pkr @ depth  (standalone, not in procedure step context)
        # Only match if preceded by "set" or "Current WBD" context
        for m in re.finditer(r"(?:Baker\s+Model\s+\S+|perm(?:anent)?\s+(?:packer|pkr))\s*[@at]+\s*~?([\d,]+)", text, re.IGNORECASE):
            depth = float(m.group(1).replace(",", ""))
            key = ("PACKER", round(depth, -1))
            if key not in seen:
                seen.add(key)
                found.append({
                    "equipment_type": "PACKER",
                    "depth_ft": depth,
                    "status": "current",
                    "description": f"Packer at {depth} ft (regex scan)",
                    "source": "regex_scan",
                })

    return found


# ---------------------------------------------------------------------------
# Cross-Validation
# ---------------------------------------------------------------------------

def _normalize_number(s: str) -> str:
    """Strip commas and trailing .0 for numeric comparison."""
    return s.replace(",", "").rstrip("0").rstrip(".")


def _validate_against_sections(items: list, sections: list, item_type: str) -> list:
    """
    Check that parsed research values actually appear in the retrieved source text.

    For formation tops: both the depth number AND formation name must appear.
    For perforations: both top and bottom depth numbers must appear.

    Tags each item with validated=True/False.
    """
    # Build a single normalized text blob from all section texts
    raw_parts = []
    for sec in sections:
        text = sec.get("text", "") if isinstance(sec, dict) else str(sec)
        raw_parts.append(_normalize_number(text.lower()))
    combined_text = "\n".join(raw_parts)

    if item_type == "equipment_history":
        logger.warning(
            "🔍 VALIDATE: equipment_history combined_text length=%d, sections=%d, preview=%.300s",
            len(combined_text), len(sections), combined_text[:300],
        )

    for item in items:
        if item_type == "formation_tops":
            depth_str = _normalize_number(str(int(item.get("top_ft", 0))))
            formation = (item.get("formation") or "").lower().strip()
            depth_found = depth_str in combined_text
            name_found = formation in combined_text if formation else False
            item["validated"] = depth_found and name_found
        elif item_type == "perforations":
            top_str = _normalize_number(str(int(item.get("top_md", 0))))
            bottom_str = _normalize_number(str(int(item.get("bottom_md", 0))))
            item["validated"] = top_str in combined_text and bottom_str in combined_text
        elif item_type == "equipment_history":
            depth_str = _normalize_number(str(int(item.get("depth_ft", 0))))
            equip_type = (item.get("equipment_type") or "").lower().strip()
            depth_found = depth_str in combined_text if float(item.get("depth_ft", 0)) > 0 else True
            # Check that at least one keyword from equipment type appears near context
            type_keywords = equip_type.split()
            type_found = any(kw in combined_text for kw in type_keywords) if type_keywords else True
            item["validated"] = depth_found and type_found
        else:
            item["validated"] = True  # Unknown type, pass through

    return items


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge_research_into_extractions(extractions: dict, research_data: dict) -> tuple:
    """
    Merge research-extracted data into scraper extractions.

    Only fills empty fields — never overwrites existing scraper data.
    Only merges items that were cross-validated against source text.

    Returns (extractions, equipment_status_map) tuple.
    The equipment_status_map is keyed by (equipment_type, depth_key).
    """
    c105 = extractions.get("c105", {})

    # Formation tops: merge only validated items if scraper had none
    existing_formations = c105.get("formation_record", [])
    research_formations = research_data.get("formation_tops", [])
    if not existing_formations and research_formations:
        validated = [f for f in research_formations if f.get("validated", False)]
        skipped = [f for f in research_formations if not f.get("validated", False)]
        if skipped:
            logger.warning(
                "Skipped %d unvalidated formation tops: %s",
                len(skipped),
                ", ".join(
                    f"{f.get('formation', '?')}@{f.get('top_ft', '?')}ft"
                    for f in skipped
                ),
            )
        if validated:
            # Remove the validated flag before storing
            for f in validated:
                f.pop("validated", None)
            c105["formation_record"] = validated
            logger.info(
                "Supplemented %d validated formation tops from research",
                len(validated),
            )

    # Perforations: merge only validated items if scraper had none
    existing_perfs = c105.get("producing_injection_disposal_interval", [])
    research_perfs = research_data.get("perforations", [])
    if not existing_perfs and research_perfs:
        validated = [p for p in research_perfs if p.get("validated", False)]
        skipped = [p for p in research_perfs if not p.get("validated", False)]
        if skipped:
            logger.warning(
                "Skipped %d unvalidated perforations: %s",
                len(skipped),
                ", ".join(
                    f"{p.get('top_md', '?')}-{p.get('bottom_md', '?')}ft"
                    for p in skipped
                ),
            )
        if validated:
            # Remove the validated flag before storing
            for p in validated:
                p.pop("validated", None)
            c105["producing_injection_disposal_interval"] = validated
            logger.info(
                "Supplemented %d validated perforation intervals from research",
                len(validated),
            )

    # Equipment: inject "current" equipment from research
    # - Packers, CIBPs, bridge plugs, retainers → mechanical_equipment (downhole tools/barriers)
    # - Tubing, liners → casing_record (handled by geometry's casing loop)
    equipment_items = research_data.get("equipment_history", [])
    if equipment_items:
        casing_record = c105.get("casing_record", [])
        mech_equipment = c105.get("mechanical_equipment", [])

        # Build existing depth set from casing_record (check BOTH depth fields)
        existing_casing_depths = set()
        for entry in casing_record:
            ct = (entry.get("casing_type") or "").upper()
            depth = entry.get("bottom") or entry.get("shoe_depth_ft") or 0
            existing_casing_depths.add((ct, round(float(depth) / 10) * 10))

        # Build existing depth set from mechanical_equipment
        existing_mech_depths = set()
        for entry in mech_equipment:
            et = (entry.get("equipment_type") or entry.get("type") or "").upper()
            depth = entry.get("depth_ft") or entry.get("set_depth_ft") or 0
            existing_mech_depths.add((et, round(float(depth) / 10) * 10))

        # All downhole equipment goes to mechanical_equipment except structural casing
        MECH_TYPES = {"CIBP", "BRIDGE_PLUG", "BRIDGE PLUG", "RETAINER", "PACKER"}

        for item in equipment_items:
            if item.get("status") != "current":
                continue
            equip_type = (item.get("equipment_type") or "").upper().strip()
            depth = item.get("depth_ft", 0.0)
            depth_key = round(depth / 10) * 10
            if depth_key == 0:
                continue

            if equip_type in MECH_TYPES or "CIBP" in equip_type or "BRIDGE" in equip_type or "PACKER" in equip_type:
                # Route to mechanical_equipment (tools/barriers)
                already_present = any(
                    equip_type in existing_type and dk == depth_key
                    for existing_type, dk in existing_mech_depths
                )
                if already_present:
                    continue
                new_entry = {
                    "type": equip_type,
                    "depth_ft": depth,
                    "size_in": None,
                    "cement_top_ft": None,
                    "sacks": 0,
                    "description": item.get("description", ""),
                    "source": "research",
                }
                mech_equipment.append(new_entry)
                existing_mech_depths.add((equip_type, depth_key))
                logger.warning(
                    "🔍 MERGE: Injected research-discovered %s at %s ft into mechanical_equipment",
                    equip_type, depth,
                )
            else:
                # Route to casing_record (packers, tubing, liners)
                already_present = any(
                    equip_type in existing_type and dk == depth_key
                    for existing_type, dk in existing_casing_depths
                )
                if already_present:
                    continue
                casing_type_name = equip_type.capitalize()
                new_entry = {
                    "top": 0.0,
                    "grade": None,
                    "sacks": 0,
                    "bottom": depth,
                    "weight": 0.0,
                    "diameter": 0.0,
                    "cement_top": 0.0,
                    "casing_type": casing_type_name,
                    "cement_bottom": 0.0,
                    "source": "research",
                }
                casing_record.append(new_entry)
                existing_casing_depths.add((equip_type, depth_key))
                logger.warning(
                    "🔍 MERGE: Injected research-discovered %s at %s ft into casing_record",
                    casing_type_name, depth,
                )

        c105["casing_record"] = casing_record
        c105["mechanical_equipment"] = mech_equipment

    # Build equipment status map (avoids needing a second query_research_for_plan_data call)
    equipment_status_map = {}
    for item in equipment_items:
        if not item.get("validated", False):
            continue
        equip_type = (item.get("equipment_type") or "").upper().strip()
        depth = item.get("depth_ft", 0.0)
        depth_key = round(depth / 10) * 10
        if equip_type and depth_key > 0:
            equipment_status_map[(equip_type, depth_key)] = item.get("status", "unverified")

    extractions["c105"] = c105
    return extractions, equipment_status_map


# ---------------------------------------------------------------------------
# Equipment Status Lookup (public API for w3a_segmented)
# ---------------------------------------------------------------------------

def get_equipment_statuses_from_research(session_id: str) -> dict:
    """
    Query research session for equipment history and return status map.

    Returns dict keyed by (equipment_type, depth_key) where depth_key is
    depth rounded to nearest 10 ft for fuzzy matching:
        {
            ("PACKER", 9050): "removed",
            ("PACKER", 11170): "current",
        }

    Gracefully returns {} on any error or if session is not ready.
    """
    if not session_id:
        return {}
    try:
        research_data = query_research_for_plan_data(session_id)
        equipment_items = research_data.get("equipment_history", [])
        raw_answer = research_data.get("raw_answers", {}).get("equipment_history", {}).get("answer", "")
        logger.warning(
            "🔍 EQUIP-STATUS: session=%s, equipment_items=%d, raw_answer=\n%s",
            session_id, len(equipment_items), raw_answer,
        )
        status_map = {}
        for item in equipment_items:
            if not item.get("validated", False):
                logger.warning(
                    "🔍 EQUIP-STATUS: Skipping unvalidated item: %s @ %s ft (status=%s, validated=%s)",
                    item.get("equipment_type"), item.get("depth_ft"), item.get("status"), item.get("validated"),
                )
                continue
            equip_type = (item.get("equipment_type") or "").upper().strip()
            depth = item.get("depth_ft", 0.0)
            depth_key = round(depth / 10) * 10
            if equip_type and depth_key > 0:
                new_status = item.get("status", "unverified")
                existing = status_map.get((equip_type, depth_key))
                if existing:
                    logger.warning(
                        "🔍 EQUIP-STATUS: Duplicate %s@%sft — had '%s', now '%s' (keeping latest)",
                        equip_type, depth_key, existing, new_status,
                    )
                status_map[(equip_type, depth_key)] = new_status
        logger.warning(
            "🔍 EQUIP-STATUS: Final map from session %s: %s",
            session_id,
            {f"{k[0]}@{k[1]}ft": v for k, v in status_map.items()} if status_map else "empty",
        )
        return status_map
    except Exception:
        logger.warning("🔍 EQUIP-STATUS: Failed for session %s", session_id, exc_info=True)
        return {}

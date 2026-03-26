"""AI-powered justification extraction from daily work records.

Scans parsed daily field tickets for evidence that explains reconciliation
discrepancies (agency approvals, field conditions, corrective actions).
"""

import json
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


def extract_justifications_from_daily_logs(
    comparisons: List[dict],
    parse_result: dict,
    api_number: str = "",
) -> Dict[str, dict]:
    """Extract justifying evidence from daily logs for each discrepancy.

    Args:
        comparisons: List of PlugComparison-as-dict objects (non-MATCH only).
        parse_result: The session's parsed ticket data (has "days" key).
        api_number: Well API number for context.

    Returns:
        Dict mapping plug_number (str) to justification info dict:
        {
            "note": "quoted evidence text",
            "source_days": ["2024-01-15", ...],
            "source_type": "agency_approval",
            "confidence": 0.85,
            "ai_suggested": True,
            "resolved": False,
        }
        Returns {} on any failure (non-fatal).
    """
    try:
        if not comparisons or not parse_result:
            return {}

        days = parse_result.get("days", [])
        if not days:
            return {}

        # Build compact daily log text
        daily_log_text = _build_daily_log_text(days)
        if not daily_log_text.strip():
            return {}

        # Build discrepancy summary
        discrepancy_text = _build_discrepancy_summary(comparisons)

        # Single AI call
        from apps.public_core.services.openai_config import (
            get_openai_client,
            DEFAULT_CLASSIFIER_MODEL,
        )

        client = get_openai_client(operation="justification_extraction")

        system_prompt = (
            "You are a regulatory compliance analyst. Your ONLY job is to find "
            "text in the DAILY WORK RECORDS that explains why field operations "
            "deviated from the planned plugging program.\n\n"
            "CRITICAL RULES:\n"
            "1. Your note MUST be a direct quote or close paraphrase from the "
            "   DAILY WORK RECORDS section ONLY. Copy the relevant sentences.\n"
            "2. NEVER repeat or rephrase the discrepancy descriptions — those "
            "   describe the PROBLEM, not the JUSTIFICATION.\n"
            "3. Look for these types of evidence in the daily logs:\n"
            "   - Agency/inspector approvals (e.g., 'David Alvarado, NMOCD, "
            "     approved to spot 75 sxs class C cement')\n"
            "   - Field conditions that forced a change (e.g., 'bad casing from "
            "     6239\\' to 5714\\', could not get L-80 packer to set')\n"
            "   - Corrective actions taken (e.g., 'drill out to 1100\\' due to "
            "     BLM error on permitted depth')\n"
            "   - Operational decisions (e.g., 'decided to squeeze instead of "
            "     set plug due to lost circulation')\n"
            "4. Include the inspector/agency name when present.\n"
            "5. If no evidence in the daily logs explains a discrepancy, return "
            "   an EMPTY string for note with source_type 'none' and confidence 0.0. "
            "   Do NOT make something up.\n\n"
            "RESPONSE FORMAT: JSON object where keys are plug numbers (strings), "
            "values have: note (string — quoted from daily logs or empty), "
            "source_days (list of date strings where evidence was found), "
            "source_type (one of: agency_approval, field_condition, "
            "corrective_action, combined, none), confidence (float 0.0-1.0)."
        )

        user_prompt = (
            f"Well: {api_number}\n\n"
            f"=== DAILY WORK RECORDS (search THIS section for evidence) ===\n"
            f"{daily_log_text}\n\n"
            f"=== DISCREPANCIES TO JUSTIFY (do NOT echo these back) ===\n"
            f"{discrepancy_text}\n\n"
            "For each plug number, search the DAILY WORK RECORDS above for text "
            "that explains WHY the deviation occurred. Quote the daily log text. "
            "If no evidence exists for a plug, set note to empty string."
        )

        response = client.chat.completions.create(
            model=DEFAULT_CLASSIFIER_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=4000,
        )

        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)

        # Normalize the response
        result = {}
        for plug_key, info in parsed.items():
            if not isinstance(info, dict):
                continue
            note = info.get("note", "").strip()
            if not note:
                continue
            result[str(plug_key)] = {
                "note": note,
                "source_days": info.get("source_days", []),
                "source_type": info.get("source_type", "none"),
                "confidence": min(1.0, max(0.0, float(info.get("confidence", 0.0)))),
                "ai_suggested": True,
                "resolved": False,
            }

        logger.info(
            "extract_justifications_from_daily_logs: api=%s found %d justifications",
            api_number, len(result),
        )
        return result

    except Exception:
        logger.exception(
            "extract_justifications_from_daily_logs: non-fatal error for api=%s",
            api_number,
        )
        return {}


def _build_daily_log_text(days: list) -> str:
    """Build compact text representation of daily logs."""
    lines = []
    for day in days:
        if not isinstance(day, dict):
            continue
        date = day.get("work_date", "unknown date")
        narrative = day.get("daily_narrative", "")

        day_lines = [f"--- Day: {date} ---"]
        if narrative:
            day_lines.append(f"Narrative: {narrative}")

        for event in day.get("events", []):
            if not isinstance(event, dict):
                continue
            desc = event.get("description", "")
            etype = event.get("event_type", "")
            if desc:
                day_lines.append(f"  [{etype}] {desc}")

        lines.extend(day_lines)

    return "\n".join(lines)


def _build_discrepancy_summary(comparisons: list) -> str:
    """Build compact text summary of discrepancies.

    Only includes plug number, deviation level, and planned vs actual
    depths/sacks. Does NOT include deviation_notes to prevent the AI
    from echoing them back instead of quoting daily log text.
    """
    lines = []
    for comp in comparisons:
        if not isinstance(comp, dict):
            continue
        plug_num = comp.get("plug_number", "?")
        level = comp.get("deviation_level", "unknown")

        planned_top = comp.get("planned_top_ft")
        planned_bottom = comp.get("planned_bottom_ft")
        planned_sacks = comp.get("planned_sacks")
        planned_type = comp.get("planned_type", "")
        actual_top = comp.get("actual_top_ft")
        actual_bottom = comp.get("actual_bottom_ft")
        actual_sacks = comp.get("actual_sacks")
        actual_type = comp.get("actual_type", "")

        line = f"Plug #{plug_num} [{level}]:"
        if planned_type:
            line += f" Planned {planned_type}"
        if planned_top is not None or planned_bottom is not None:
            line += f" {planned_top}'-{planned_bottom}'"
        if planned_sacks is not None:
            line += f" ({planned_sacks} sacks)"
        if actual_type:
            line += f" → Actual {actual_type}"
        if actual_top is not None or actual_bottom is not None:
            line += f" {actual_top}'-{actual_bottom}'"
        if actual_sacks is not None:
            line += f" ({actual_sacks} sacks)"

        lines.append(line)

    return "\n".join(lines)

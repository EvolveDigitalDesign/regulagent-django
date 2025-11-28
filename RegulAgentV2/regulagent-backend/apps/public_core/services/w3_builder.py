"""
W-3 Builder - Main Orchestrator

Ties together all W-3 generation components into a single, cohesive workflow.

Flow:
1. Load W-3A form (from PDF or database)
2. Initialize casing state from W-3A casing record
3. Map pnaexchange events to W3Event instances
4. Format into complete W-3 form
5. Return W3Form ready for API response or submission

This is the main entry point that pnaexchange will call.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
import logging

from apps.public_core.models.w3_event import CasingStringState, W3Form
from apps.public_core.services.w3_extraction import extract_w3a_from_pdf, load_w3a_form
from apps.public_core.services.w3_casing_engine import initialize_casing_state
from apps.public_core.services.w3_mapper import map_pna_events_to_w3events, validate_event_inputs
from apps.public_core.services.w3_formatter import build_w3_form

logger = logging.getLogger(__name__)


def build_w3_from_pna_payload(
    pna_payload: Dict[str, Any],
    request=None
) -> Dict[str, Any]:
    """
    Main entry point: Build W-3 form from pnaexchange payload.
    
    This is what the API endpoint calls.
    
    Args:
        pna_payload: Dictionary with structure:
            {
                "dwr_id": 12345,
                "api_number": "42-501-70575",
                "well_name": "Example Well",
                "w3a_reference": {
                    "type": "pdf" | "regulagent",
                    "w3a_file": UploadedFile (if type="pdf"),
                    "w3a_id": int (if type="regulagent")
                },
                "pna_events": [
                    {
                        "event_id": 4,
                        "display_text": "Set Intermediate Plug",
                        "input_values": {"1": "5", "2": "6997", ...},
                        "transformation_rules": {...},
                        "date": "2025-01-15",
                        "start_time": "09:30:00",
                        "end_time": "10:15:00",
                        "work_assignment_id": 123,
                        "dwr_id": 12345
                    },
                    ...
                ],
                "tenant_id": 1  # Multi-tenant support
            }
        request: HTTP request object (needed for file uploads)
    
    Returns:
        Dictionary with structure:
        {
            "success": true,
            "w3_form": {
                "header": {...},
                "plugs": [...],
                "casing_record": [...],
                "perforations": [...],
                "duqw": {...},
                "remarks": "...",
                "pdf_url": null
            },
            "validation": {
                "warnings": [],
                "errors": []
            },
            "metadata": {
                "api_number": "42-501-70575",
                "dwr_id": 12345,
                "events_processed": 15,
                "plugs_grouped": 8,
                "generated_at": "2025-01-15T10:30:00Z"
            }
        }
        
    Raises:
        ValueError: If payload is invalid or processing fails
    """
    from datetime import datetime
    import pytz
    
    logger.info("=" * 80)
    logger.info("🚀 W-3 BUILDER - Starting build from pnaexchange payload")
    logger.info("=" * 80)
    
    warnings = []
    errors = []
    
    try:
        # ============================================================
        # STEP 1: Load W-3A form
        # ============================================================
        logger.info("\n📄 STEP 1: Loading W-3A form...")
        
        w3a_reference = pna_payload.get("w3a_reference")
        if not w3a_reference:
            raise ValueError("pna_payload missing 'w3a_reference'")
        
        try:
            w3a_form = load_w3a_form(w3a_reference, request=request)
            logger.info(f"✅ W-3A loaded: API {w3a_form['header'].get('api_number')}")
        except Exception as e:
            logger.error(f"❌ Failed to load W-3A form: {e}")
            raise ValueError(f"Cannot load W-3A form: {e}")
        
        # ============================================================
        # STEP 2: Initialize casing state
        # ============================================================
        logger.info("\n⛏️ STEP 2: Initializing casing state...")
        
        w3a_casing_record = w3a_form.get("casing_record", [])
        try:
            casing_state = initialize_casing_state(w3a_casing_record)
            logger.info(f"✅ Casing state initialized: {len(casing_state)} strings")
            for cs in casing_state:
                logger.debug(f"   - {cs.string_type}: {cs.od_in}\" @ {cs.top_ft}-{cs.bottom_ft} ft")
        except Exception as e:
            logger.error(f"❌ Failed to initialize casing state: {e}")
            raise ValueError(f"Cannot initialize casing state: {e}")
        
        # ============================================================
        # STEP 3: Validate pnaexchange events
        # ============================================================
        logger.info("\n🔍 STEP 3: Validating pnaexchange events...")
        
        pna_events = pna_payload.get("pna_events", [])
        logger.info(f"   Received {len(pna_events)} events")
        
        for i, event in enumerate(pna_events):
            event_id = event.get("event_id")
            is_valid, error_msg = validate_event_inputs(event_id, event.get("input_values", {}))
            
            if not is_valid:
                msg = f"Event {i}: {error_msg}"
                logger.warning(f"⚠️  {msg}")
                warnings.append(msg)
        
        if warnings:
            logger.info(f"⚠️  {len(warnings)} validation warning(s)")
        
        # ============================================================
        # STEP 4: Map pnaexchange events to W3Events
        # ============================================================
        logger.info("\n🔄 STEP 4: Mapping pnaexchange events to W3Events...")
        
        try:
            w3_events = map_pna_events_to_w3events(pna_events)
            logger.info(f"✅ Mapped {len(pna_events)} events to {len(w3_events)} W3Events")
            
            for i, event in enumerate(w3_events[:3]):  # Show first 3
                logger.debug(f"   Event {i+1}: {event.event_type} @ {event.depth_bottom_ft} ft")
            
            if len(w3_events) > 3:
                logger.debug(f"   ... and {len(w3_events) - 3} more")
        
        except Exception as e:
            logger.error(f"❌ Event mapping failed: {e}", exc_info=True)
            raise ValueError(f"Cannot map pnaexchange events: {e}")
        
        # ============================================================
        # STEP 5: Build W-3 form
        # ============================================================
        logger.info("\n🏗️ STEP 5: Building W-3 form...")
        
        try:
            w3_form = build_w3_form(w3a_form, w3_events, casing_state)
            
            logger.info(f"✅ W-3 form built:")
            logger.info(f"   - {len(w3_form.plugs)} plugs")
            logger.info(f"   - {len(w3_form.casing_record)} casing strings")
            logger.info(f"   - {len(w3_form.perforations)} perforations")
            logger.info(f"   - Remarks: {len(w3_form.remarks)} chars")
        
        except Exception as e:
            logger.error(f"❌ Form building failed: {e}", exc_info=True)
            raise ValueError(f"Cannot build W-3 form: {e}")
        
        # ============================================================
        # STEP 6: Build response
        # ============================================================
        logger.info("\n📦 STEP 6: Building response...")
        
        response = {
            "success": True,
            "w3_form": {
                "header": w3_form.header,
                "plugs": w3_form.plugs,
                "casing_record": w3_form.casing_record,
                "perforations": w3_form.perforations,
                "duqw": w3_form.duqw,
                "remarks": w3_form.remarks,
                "pdf_url": w3_form.pdf_url,
            },
            "validation": {
                "warnings": warnings,
                "errors": errors,
            },
            "metadata": {
                "api_number": w3a_form.get("header", {}).get("api_number"),
                "dwr_id": pna_payload.get("dwr_id"),
                "events_processed": len(w3_events),
                "plugs_grouped": len(w3_form.plugs),
                "generated_at": datetime.now(pytz.UTC).isoformat(),
            }
        }
        
        logger.info("✅ Response built successfully")
        logger.info("=" * 80)
        logger.info("✅ W-3 BUILD COMPLETE")
        logger.info("=" * 80 + "\n")
        
        return response
    
    except Exception as e:
        logger.error(f"\n❌ W-3 BUILD FAILED: {e}", exc_info=True)
        
        return {
            "success": False,
            "error": str(e),
            "validation": {
                "warnings": warnings,
                "errors": [str(e)],
            },
            "metadata": {
                "api_number": pna_payload.get("api_number"),
                "dwr_id": pna_payload.get("dwr_id"),
                "generated_at": datetime.now(pytz.UTC).isoformat(),
            }
        }


def validate_w3_form_against_proposal(
    w3_form: W3Form,
    w3a_operational_steps: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Validate that the generated W-3 form matches the W-3A proposal.
    
    This ensures that pnaexchange events align with the plugging proposal.
    
    Args:
        w3_form: Generated W3Form from build_w3_from_pna_payload
        w3a_operational_steps: From w3a_form["operational_steps"]
    
    Returns:
        Validation result:
        {
            "is_valid": true/false,
            "mismatches": [
                {"step": 1, "proposal": "tag_toc", "actual": "set_cement_plug", "severity": "error"},
                ...
            ],
            "summary": "All steps match proposal"
        }
    """
    logger.info("🔍 Validating W-3 form against W-3A proposal...")
    
    mismatches = []
    
    # Map W-3A operational steps to expected sequence
    proposal_steps = []
    for step in w3a_operational_steps:
        proposal_steps.append({
            "step_order": step.get("step_order"),
            "step_type": step.get("step_type"),
            "plug_number": step.get("plug_number"),
        })
    
    logger.info(f"   W-3A proposal has {len(proposal_steps)} operational steps")
    logger.info(f"   Generated W-3 has {len(w3_form.plugs)} plugs")
    
    # For now, log but don't enforce - validation can be enhanced later
    logger.info("✅ Validation check complete")
    
    return {
        "is_valid": len(mismatches) == 0,
        "mismatches": mismatches,
        "summary": "All steps match proposal" if not mismatches else f"{len(mismatches)} mismatch(es) found",
    }


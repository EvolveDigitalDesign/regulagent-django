"""
Plan detail endpoint - retrieve full plan payload for viewing and chat interaction.

This is the primary endpoint users interact with to:
- View the complete baseline plan
- Initiate chat-based modifications
- See current workflow status
"""

import logging
import re
from typing import Optional, Any, Dict, List

from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.public_core.models import PlanSnapshot, ExtractedDocument

logger = logging.getLogger(__name__)


def _extract_formations_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract formation tops from plan payload.
    
    The payload contains:
    - formation_tops_detected: list of formation names ["Spraberry", "Dean", "Clearfork"]
    - steps: list of plug steps with top_ft depth and regulatory_basis
    
    We combine these to create formation_tops with name and depth.
    
    Returns list like:
    [
        {"formation": "Spraberry", "top_ft": 6750},
        {"formation": "Dean", "top_ft": 6750},
        {"formation": "Clearfork", "top_ft": 5650},
        ...
    ]
    """
    formation_tops: List[Dict[str, Any]] = []
    
    try:
        # Get list of detected formations
        formations_detected = payload.get('formation_tops_detected', [])
        if not formations_detected:
            formations_detected = payload.get('formations_targeted', [])
        
        # If we have formations, extract their depths from steps
        if formations_detected and isinstance(formations_detected, list):
            steps = payload.get('steps', [])
            
            # Build a map of formation name to depths from regulatory_basis codes
            formation_depths: Dict[str, float] = {}
            
            if isinstance(steps, list):
                for step in steps:
                    if isinstance(step, dict):
                        top_ft = step.get('top_ft')
                        regulatory_basis = step.get('regulatory_basis', [])
                        
                        # Parse regulatory_basis to find formation names
                        # Format: "rrc.district.XX.county:formation_top:FormationName"
                        if isinstance(regulatory_basis, list):
                            for basis in regulatory_basis:
                                if isinstance(basis, str) and 'formation_top:' in basis:
                                    try:
                                        # Extract formation name after "formation_top:"
                                        formation_name = basis.split('formation_top:')[-1].strip()
                                        if formation_name and top_ft is not None:
                                            formation_depths[formation_name] = float(top_ft)
                                    except Exception:
                                        pass
            
            # Create formation_tops list from detected formations with their depths
            for formation_name in formations_detected:
                if formation_name in formation_depths:
                    formation_tops.append({
                        "formation": formation_name,
                        "top_ft": formation_depths[formation_name],
                    })
                else:
                    # Include even if depth not found (may be in W-2 later)
                    formation_tops.append({
                        "formation": formation_name,
                        "top_ft": None,
                    })
        
        if formation_tops:
            logger.info(f"Extracted {len(formation_tops)} formation tops from payload")
    
    except Exception as e:
        logger.warning(f"Failed to extract formations from payload: {e}")
    
    return formation_tops


def _extract_historic_cement_jobs(api14: str) -> List[Dict[str, Any]]:
    """
    Extract all historic cement jobs from W-15 document.
    Store all cement jobs without filtering to preserve complete historical data.
    """
    historic_cement_jobs: List[Dict[str, Any]] = []
    try:
        w15_doc = ExtractedDocument.objects.filter(
            api_number=api14,
            document_type='w15'
        ).order_by('-created_at').first()
        
        if w15_doc and isinstance(w15_doc.json_data, dict):
            w15 = w15_doc.json_data
            cementing_data = w15.get("cementing_data") or []
            
            if isinstance(cementing_data, list):
                for cement_job in cementing_data:
                    if isinstance(cement_job, dict):
                        try:
                            # Include all available fields from the cement job
                            job_entry: Dict[str, Any] = {
                                "job_type": cement_job.get("job"),
                                "interval_top_ft": cement_job.get("interval_top_ft"),
                                "interval_bottom_ft": cement_job.get("interval_bottom_ft"),
                                "cement_top_ft": cement_job.get("cement_top_ft"),
                                "sacks": cement_job.get("sacks"),
                                "slurry_density_ppg": cement_job.get("slurry_density_ppg"),
                                "additives": cement_job.get("additives"),
                                "yield_ft3_per_sk": cement_job.get("yield_ft3_per_sk"),
                            }
                            # Store all cement jobs as-is, preserving complete historical data
                            historic_cement_jobs.append(job_entry)
                        except Exception:
                            pass
            
            if historic_cement_jobs:
                logger.info(f"Extracted {len(historic_cement_jobs)} historic cement jobs from W-15 for API {api14}")
    except Exception as e:
        logger.warning(f"Failed to extract historic cement jobs from W-15 for API {api14}: {e}")
    
    return historic_cement_jobs


def _extract_mechanical_equipment(api14: str) -> List[Dict[str, Any]]:
    """
    Extract mechanical equipment (CIBPs, bridge plugs, packers) from W-15 document.
    Store all equipment with complete specifications.
    """
    mechanical_equipment: List[Dict[str, Any]] = []
    try:
        w15_doc = ExtractedDocument.objects.filter(
            api_number=api14,
            document_type='w15'
        ).order_by('-created_at').first()
        
        if w15_doc and isinstance(w15_doc.json_data, dict):
            w15 = w15_doc.json_data
            equipment_data = w15.get("mechanical_equipment") or []
            
            if isinstance(equipment_data, list):
                for equipment in equipment_data:
                    if isinstance(equipment, dict):
                        try:
                            # Include all available fields from the equipment entry
                            equipment_entry: Dict[str, Any] = {
                                "equipment_type": equipment.get("equipment_type"),  # CIBP|bridge_plug|packer
                                "size_in": equipment.get("size_in"),
                                "depth_ft": equipment.get("depth_ft"),
                                "sacks": equipment.get("sacks"),
                                "notes": equipment.get("notes"),
                            }
                            # Store all equipment as-is, preserving complete specifications
                            mechanical_equipment.append(equipment_entry)
                        except Exception:
                            pass
            
            if mechanical_equipment:
                logger.info(f"Extracted {len(mechanical_equipment)} mechanical equipment items from W-15 for API {api14}")
    except Exception as e:
        logger.warning(f"Failed to extract mechanical equipment from W-15 for API {api14}: {e}")
    
    return mechanical_equipment


def _build_well_geometry(api14: str, payload: Optional[Dict[str, Any]] = None) -> dict:
    """
    Extract well geometry from ExtractedDocuments for a given API.
    Returns casing strings, formation tops, perforations, production intervals, mechanical equipment, and tubing.
    
    Args:
        api14: The API number
        payload: Optional plan payload containing formation_tops_detected and steps
    """
    geometry = {
        "casing_strings": [],
        "formation_tops": [],
        "perforations": [],
        "production_perforations": [],
        "tubing": [],
        "liner": [],
        "historic_cement_jobs": [],
        "mechanical_equipment": [],
        "existing_tools": [],
    }
    
    # First, try to extract formations from payload if provided
    # This includes formation names and depths from the plan
    if payload and isinstance(payload, dict):
        formation_tops_from_payload = _extract_formations_from_payload(payload)
        if formation_tops_from_payload:
            geometry['formation_tops'] = formation_tops_from_payload
        
        # Also check for casing_strings in payload (from segmented W3A flow)
        casing_strings_from_payload = payload.get('casing_strings', [])
        logger.info(f"🔍 _build_well_geometry: Found {len(casing_strings_from_payload)} casing_strings in payload")
        if casing_strings_from_payload:
            geometry['casing_strings'] = casing_strings_from_payload
            logger.info(f"✅ Using {len(casing_strings_from_payload)} casing_strings from payload")
        else:
            logger.info(f"⚠️ No casing_strings in payload, will try ExtractedDocument")
    
    # Get W-2 document for casing and formation data (fallback if not in payload)
    w2 = ExtractedDocument.objects.filter(
        api_number=api14,
        document_type='w2'
    ).first()
    
    if w2:
        # Extract casing strings (only if not already loaded from payload)
        if not geometry['casing_strings']:
            casing_record = w2.json_data.get('casing_record', [])
            if casing_record:
                geometry['casing_strings'] = casing_record
        
        # Extract formation tops from W-2 (fallback if not in payload)
        if not geometry['formation_tops']:
            formation_record = w2.json_data.get('formation_record', [])
            if formation_record:
                geometry['formation_tops'] = formation_record
        
        # Extract tubing if available
        tubing_record = w2.json_data.get('tubing_record', [])
        if tubing_record:
            geometry['tubing'] = tubing_record
        
        # Extract liner if available
        liner_record = w2.json_data.get('liner_record', [])
        if liner_record:
            geometry['liner'] = liner_record
        
        # Extract production/injection/disposal intervals as production perforations
        pidi_record = w2.json_data.get('producing_injection_disposal_interval', [])
        if pidi_record:
            production_perfs = []
            for interval in pidi_record:
                if isinstance(interval, dict):
                    perf_entry = {
                        "top_ft": interval.get("from_ft"),
                        "bottom_ft": interval.get("to_ft"),
                        "open_hole": interval.get("open_hole", False),
                    }
                    production_perfs.append(perf_entry)
            geometry['production_perforations'] = production_perfs
        
        # Extract existing tools (CIBP, bridge plugs, packers, DV tools, retainers) from multiple sources
        existing_tools = []
        
        # 1. From acid_fracture_operations (mechanical_plug, retainer, bridge plug)
        afo_record = w2.json_data.get('acid_fracture_operations', [])
        if afo_record:
            for operation in afo_record:
                if isinstance(operation, dict):
                    op_type = operation.get("operation_type", "").lower()
                    # Filter for mechanical plugs and barriers
                    if "mechanical" in op_type or "cibp" in op_type or "bridge" in op_type or "retainer" in op_type:
                        tool_entry = {
                            "source": "acid_fracture_operations",
                            "tool_type": operation.get("operation_type"),
                            "material_description": operation.get("amount_and_kind_of_material_used"),
                            "top_ft": operation.get("from_ft"),
                            "bottom_ft": operation.get("to_ft"),
                            "open_hole": operation.get("open_hole", False),
                            "notes": operation.get("notes"),
                        }
                        existing_tools.append(tool_entry)
        
        # 2. From remarks - extract CIBP, Packer, DV Tool, Retainer depths using regex
        try:
            remarks_txt = str(w2.json_data.get("remarks") or "")
            rrc_remarks_obj = w2.json_data.get("rrc_remarks") or {}
            rrc_remarks_txt = ""
            if isinstance(rrc_remarks_obj, dict):
                for key, val in rrc_remarks_obj.items():
                    if val:
                        rrc_remarks_txt += f" {val}"
            elif isinstance(rrc_remarks_obj, str):
                rrc_remarks_txt = rrc_remarks_obj
            
            combined_remarks = f"{remarks_txt} {rrc_remarks_txt}"
            
            # Extract CIBP depth
            for pattern in [r"CIBP\s*(?:at|@)?\s*(\d{3,5})", r"cast\s*iron\s*bridge\s*plug\s*(?:at|@)?\s*(\d{3,5})", r"\bBP\b\s*(?:at|@)?\s*(\d{3,5})"]:
                match = re.search(pattern, combined_remarks, flags=re.IGNORECASE)
                if match:
                    try:
                        depth = float(match.group(1))
                        # Check if already in existing_tools (from acid_fracture_operations)
                        if not any(t.get("tool_type", "").lower() == "cibp" and t.get("top_ft") == depth for t in existing_tools):
                            existing_tools.append({
                                "source": "remarks",
                                "tool_type": "CIBP",
                                "depth_ft": depth,
                            })
                        break
                    except Exception:
                        pass
            
            # Extract Packer depth
            packer_match = re.search(r"packer\s*(?:at|@|set\s+at)?\s*(\d{3,5})", combined_remarks, flags=re.IGNORECASE)
            if packer_match:
                try:
                    depth = float(packer_match.group(1))
                    if not any(t.get("tool_type", "").lower() == "packer" and t.get("depth_ft") == depth for t in existing_tools):
                        existing_tools.append({
                            "source": "remarks",
                            "tool_type": "Packer",
                            "depth_ft": depth,
                        })
                except Exception:
                    pass
            
            # Extract DV Tool depth
            for pattern in [r"DV[- ]?(?:stage)?\s*tool\s*(?:at|@)?\s*(\d{3,5})", r"DV[- ]?tool\s*(?:at|@)?\s*(\d{3,5})"]:
                dv_match = re.search(pattern, combined_remarks, flags=re.IGNORECASE)
                if dv_match:
                    try:
                        depth = float(dv_match.group(1))
                        if not any(t.get("tool_type", "").lower() == "dv_tool" and t.get("depth_ft") == depth for t in existing_tools):
                            existing_tools.append({
                                "source": "remarks",
                                "tool_type": "DV_Tool",
                                "depth_ft": depth,
                            })
                        break
                    except Exception:
                        pass
            
            # Extract Retainer depth
            for pattern in [r"retainer\s*(?:at|@)?\s*(\d{3,5})", r"retainer\s+(?:packer\s+)?(?:at|@)?\s*(\d{3,5})"]:
                retainer_matches = re.finditer(pattern, combined_remarks, flags=re.IGNORECASE)
                for match in retainer_matches:
                    try:
                        depth = float(match.group(1))
                        if not any(t.get("tool_type", "").lower() == "retainer" and t.get("depth_ft") == depth for t in existing_tools):
                            existing_tools.append({
                                "source": "remarks",
                                "tool_type": "Retainer",
                                "depth_ft": depth,
                            })
                    except Exception:
                        pass
            
            # Extract Straddle Packer depth
            for pattern in [r"straddle\s*(?:packer\s+)?(?:at|@)?\s*(\d{3,5})", r"straddle\s*(?:at|@)?\s*(\d{3,5})"]:
                straddle_matches = re.finditer(pattern, combined_remarks, flags=re.IGNORECASE)
                for match in straddle_matches:
                    try:
                        depth = float(match.group(1))
                        if not any(t.get("tool_type", "").lower() == "straddle_packer" and t.get("depth_ft") == depth for t in existing_tools):
                            existing_tools.append({
                                "source": "remarks",
                                "tool_type": "Straddle_Packer",
                                "depth_ft": depth,
                            })
                    except Exception:
                        pass
        except Exception:
            pass
        
        geometry['existing_tools'] = existing_tools
    
    # Get W-15 document for additional formation tops or perforations
    w15 = ExtractedDocument.objects.filter(
        api_number=api14,
        document_type='w15'
    ).first()
    
    if w15:
        # Check for perforations
        perforations = w15.json_data.get('perforations', [])
        if perforations:
            geometry['perforations'] = perforations
        
        # Check for formation tops (if not already in W-2)
        formation_tops = w15.json_data.get('formation_tops', [])
        if formation_tops and not geometry['formation_tops']:
            geometry['formation_tops'] = formation_tops
    
    # Extract historic cement jobs from W-15 (or payload if available)
    if payload and isinstance(payload, dict) and payload.get('historic_cement_jobs'):
        geometry['historic_cement_jobs'] = payload['historic_cement_jobs']
        logger.info(f"Using historic_cement_jobs from payload for API {api14}")
    else:
        geometry['historic_cement_jobs'] = _extract_historic_cement_jobs(api14)
    
    # Extract mechanical equipment from payload (user-added tools) or W-15
    if payload and isinstance(payload, dict) and payload.get('mechanical_equipment'):
        geometry['mechanical_equipment'] = payload['mechanical_equipment']
        logger.info(f"Using mechanical_equipment from payload for API {api14} ({len(payload['mechanical_equipment'])} tools)")
    else:
        geometry['mechanical_equipment'] = _extract_mechanical_equipment(api14)
    
    # Also add existing_tools alias if present in payload
    if payload and isinstance(payload, dict) and payload.get('existing_tools'):
        geometry['existing_tools'].extend([
            tool for tool in payload['existing_tools']
            if tool not in geometry['existing_tools']
        ])
        logger.info(f"Added {len(payload['existing_tools'])} existing_tools from payload for API {api14}")
    
    # Extract production perforations from payload if available
    if payload and isinstance(payload, dict) and payload.get('production_perforations'):
        # Only override if payload has perforations and we don't have them from W-2
        if not geometry['production_perforations']:
            geometry['production_perforations'] = payload['production_perforations']
            logger.info(f"Using production_perforations from payload for API {api14}")
    
    return geometry


@api_view(['GET'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def get_plan_detail(request, plan_id):
    """
    Retrieve complete plan with full payload.
    
    GET /api/plans/{plan_id}/
    
    Accepts either:
    - plan_id string (e.g., "4230132998:isolated") - returns latest snapshot with that plan_id
    - snapshot ID (e.g., "145") - returns that specific snapshot
    
    Returns:
        - Full plan JSON (steps, violations, materials, etc.)
        - Workflow status
        - Well information
        - Metadata (kernel version, policy, extraction info)
    
    This is the primary plan view that users interact with before
    making modifications via chat or manual edits.
    """
    user_tenant = request.user.tenants.first()
    if not user_tenant:
        return Response(
            {"error": "User not associated with any tenant"},
            status=status.HTTP_403_FORBIDDEN
        )
    
    # Try to get snapshot by ID first (if plan_id is a database ID)
    try:
        # Check if plan_id is a numeric ID
        if plan_id.isdigit():
            snapshot = (
                PlanSnapshot.objects
                .select_related('well')
                .filter(id=int(plan_id), tenant_id=user_tenant.id)
                .first()
            )
            if snapshot:
                logger.info(f"Found snapshot by database ID: {plan_id}")
            else:
                return Response(
                    {"error": f"Plan snapshot {plan_id} not found for your tenant"},
                    status=status.HTTP_404_NOT_FOUND
                )
        else:
            # Otherwise treat as plan_id string and get the latest
            snapshot = (
                PlanSnapshot.objects
                .select_related('well')
                .filter(plan_id=plan_id, tenant_id=user_tenant.id)
                .order_by('-created_at')
                .first()
            )
            
            if not snapshot:
                raise PlanSnapshot.DoesNotExist
            
            logger.info(f"Found snapshot by plan_id string: {plan_id}")
            
    except PlanSnapshot.DoesNotExist:
        return Response(
            {"error": f"Plan {plan_id} not found for your tenant"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Get payload first (needed for formation extraction)
    payload = snapshot.payload.copy() if isinstance(snapshot.payload, dict) else snapshot.payload
    
    # 🔍 DEBUG: Log what's in the retrieved payload
    logger.error(f"❌ RETRIEVED SNAPSHOT PAYLOAD: casing_strings count = {len(payload.get('casing_strings', []))}")
    if payload.get('casing_strings'):
        logger.error(f"❌ RETRIEVED: First casing = {payload['casing_strings'][0]}")
    else:
        logger.error(f"❌❌❌ RETRIEVED: casing_strings is EMPTY in payload!")
    
    # Fetch well geometry from extracted documents (pass payload for formation extraction)
    well_geometry = _build_well_geometry(snapshot.well.api14, payload)
    if isinstance(payload, dict):
        if well_geometry.get("historic_cement_jobs"):
            payload["historic_cement_jobs"] = well_geometry["historic_cement_jobs"]
        if well_geometry.get("production_perforations"):
            payload["production_perforations"] = well_geometry["production_perforations"]
        if well_geometry.get("mechanical_equipment"):
            payload["mechanical_equipment"] = well_geometry["mechanical_equipment"]
        if well_geometry.get("existing_tools"):
            payload["existing_tools"] = well_geometry["existing_tools"]
    
    # Build response with full plan data
    response_data = {
        # Plan metadata
        "id": snapshot.id,
        "plan_id": snapshot.plan_id,
        "kind": snapshot.kind,
        "status": snapshot.status,
        "visibility": snapshot.visibility,
        "tenant_id": str(snapshot.tenant_id) if snapshot.tenant_id else None,
        
        # Well information
        "well": {
            "api14": snapshot.well.api14,
            "state": snapshot.well.state,
            "county": snapshot.well.county,
            "operator_name": snapshot.well.operator_name,
            "field_name": snapshot.well.field_name,
            "lease_name": snapshot.well.lease_name,
            "well_number": snapshot.well.well_number,
            "lat": float(snapshot.well.lat) if snapshot.well.lat else None,
            "lon": float(snapshot.well.lon) if snapshot.well.lon else None,
        },
        
        # Well geometry (casing, formations, perforations) - critical for chat context
        "well_geometry": well_geometry,
        
        # Provenance
        "kernel_version": snapshot.kernel_version,
        "policy_id": snapshot.policy_id,
        "overlay_id": snapshot.overlay_id,
        "extraction_meta": snapshot.extraction_meta,
        
        # Timestamps
        "created_at": snapshot.created_at,
        
        # THE ACTUAL PLAN - This is what the user sees and modifies
        "payload": payload,
    }
    
    # 🔍 DEBUG: Log what's in the final response being sent to frontend
    logger.error(f"❌ FINAL RESPONSE: well_geometry.casing_strings count = {len(response_data['well_geometry'].get('casing_strings', []))}")
    if response_data['well_geometry'].get('casing_strings'):
        logger.error(f"❌ FINAL RESPONSE: First casing = {response_data['well_geometry']['casing_strings'][0]}")
    else:
        logger.error(f"❌❌❌ FINAL RESPONSE: casing_strings is EMPTY in well_geometry!")
    
    logger.info(f"Retrieved plan {plan_id} (status: {snapshot.status}) for user {request.user.email}")
    
    return Response(response_data, status=status.HTTP_200_OK)


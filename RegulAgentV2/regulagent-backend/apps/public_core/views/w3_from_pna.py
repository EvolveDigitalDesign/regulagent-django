"""
W-3 Form Generation API View

Endpoint: POST /api/w3/build-from-pna/

Receives operational events from pnaexchange and generates an RRC-compliant W-3 form.

Example request:
{
    "dwr_id": 12345,
    "api_number": "42-501-70575",
    "well_name": "Example Well",
    "w3a_reference": {
        "type": "pdf",
        "w3a_file": <binary PDF data>
    },
    "pna_events": [
        {
            "event_id": 4,
            "display_text": "Set Intermediate Plug",
            "input_values": {"1": "5", "2": "6997", ...},
            "date": "2025-01-15",
            ...
        },
        ...
    ]
}

Example response (success):
{
    "success": true,
    "w3_form": {
        "header": {...},
        "plugs": [...],
        "casing_record": [...],
        "perforations": [...],
        "duqw": {...},
        "remarks": "..."
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
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
import logging
import json

from apps.public_core.serializers.w3_from_pna import (
    BuildW3FromPNARequestSerializer,
    BuildW3FromPNAResponseSerializer,
)
from apps.public_core.services.w3_builder import build_w3_from_pna_payload

logger = logging.getLogger(__name__)


class BuildW3FromPNAView(APIView):
    """
    API endpoint for generating W-3 forms from pnaexchange events.
    
    POST /api/w3/build-from-pna/
    
    Authentication: JWT token (pnaexchange API key)
    Content-Type: multipart/form-data (for PDF upload) or application/json (with base64 PDF)
    """
    
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    
    def post(self, request, *args, **kwargs):
        """
        Handle POST request to generate W-3 form.
        
        Args:
            request: DRF Request object
            
        Returns:
            Response with W-3 form data or error
        """
        import sys
        print("=" * 80, file=sys.stderr)
        print("🔵 W-3 API POST METHOD CALLED", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        print(f"Request keys: {list(request.data.keys())}", file=sys.stderr)
        
        # Log raw payload before any processing - use stderr so it shows
        print("\n📦 RAW PAYLOAD DUMP:", file=sys.stderr)
        # Don't include full PDF base64 - just show structure
        payload_for_print = dict(request.data)
        if 'w3a_reference' in payload_for_print and isinstance(payload_for_print['w3a_reference'], dict):
            w3a_ref = dict(payload_for_print['w3a_reference'])
            if 'w3a_file_base64' in w3a_ref:
                w3a_ref['w3a_file_base64'] = '<base64 PDF data - truncated>'
            payload_for_print['w3a_reference'] = w3a_ref
        print(json.dumps(payload_for_print, indent=2, default=str), file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        
        logger.info("=" * 80)
        logger.info("🔵 W-3 API REQUEST RECEIVED")
        logger.info("=" * 80)
        logger.info(f"   Request content-type: {request.content_type}")
        logger.info(f"   Request data keys: {list(request.data.keys())}")
        
        # DEBUG: Check payload structure and unwrap if needed
        data = request.data
        if hasattr(request.data, "copy"):
            data = request.data.copy()
        
        # Handle pnaexchange payload wrapped in w3_form
        if 'w3_form' in data and 'pna_events' not in data:
            logger.info("   ⚠️  Payload has 'w3_form' wrapper - unwrapping...")
            w3_form_data = data.get('w3_form', {})
            logger.info(f"      w3_form keys: {list(w3_form_data.keys())}")
            
            # Extract well info from wrapper
            well_data = w3_form_data.get('well', {})
            events = w3_form_data.get('events', [])
            subproject_data = w3_form_data.get('subproject', {})
            
            logger.info(f"      Well ID: {well_data.get('well_id')}")
            logger.info(f"      API Number: {well_data.get('api_number')}")
            logger.info(f"      Number of events: {len(events)}")
            logger.info(f"      Subproject ID: {subproject_data.get('id')}")
            
            # Unwrap to flat structure expected by serializer
            data['api_number'] = well_data.get('api_number') or data.get('api_number')
            data['well_name'] = well_data.get('well_name') or data.get('well_name')
            
            # Extract subproject_id from nested structure
            subproject_id = subproject_data.get('id') or data.get('subproject_id')
            if subproject_id:
                data['subproject_id'] = subproject_id
                logger.info(f"      Extracted subproject_id: {subproject_id}")
            else:
                # Fallback to legacy dwr_id or 0
                data['dwr_id'] = data.get('dwr_id') or 0
                logger.warning(f"      No subproject_id found, using dwr_id={data['dwr_id']}")
            
            data['pna_events'] = events
            
            # Add placeholder w3a_reference if not present (since this is coming from PNA after the fact)
            if 'w3a_reference' not in data:
                logger.info("      Adding placeholder w3a_reference (auto-generate mode)")
                data['w3a_reference'] = {
                    'type': 'auto',  # Signal to auto-generate W-3A
                    'w3a_file_base64': None
                }
            
            logger.info(f"      Unwrapped payload: api_number={data['api_number']}, events={len(events)}, subproject_id={data.get('subproject_id')}")
        else:
            logger.info("   ✓ Payload structure is flat (not wrapped in w3_form)")

        if isinstance(data.get("w3a_reference"), str):
            try:
                data["w3a_reference"] = json.loads(data["w3a_reference"])
                logger.info("   Parsed w3a_reference JSON string into dict")
            except json.JSONDecodeError:
                logger.warning("   Failed to parse w3a_reference string as JSON")

        if isinstance(data.get("pna_events"), str):
            try:
                data["pna_events"] = json.loads(data["pna_events"])
                logger.info("   Parsed pna_events JSON string into list")
            except json.JSONDecodeError:
                logger.warning("   Failed to parse pna_events string as JSON")

        w3a_ref = data.get("w3a_reference", {})
        if isinstance(w3a_ref, dict):
            logger.info(f"   w3a_reference keys: {list(w3a_ref.keys())}")
            logger.info(f"   w3a_file_base64 present: {'w3a_file_base64' in w3a_ref}")
            logger.info(f"   w3a_file_base64 length: {len(w3a_ref.get('w3a_file_base64', '') or '')}")

        # Deserialize and validate request
        serializer = BuildW3FromPNARequestSerializer(data=data)
        
        if not serializer.is_valid():
            logger.warning(f"❌ Request validation failed: {serializer.errors}")
            return Response(
                {
                    "success": False,
                    "error": "Invalid request",
                    "validation_errors": serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        logger.info(f"✅ Request validated")
        
        # Extract validated data
        validated_data = serializer.validated_data
        api_number = validated_data.get("api_number")
        subproject_id = validated_data.get("subproject_id")
        pna_events = validated_data.get('pna_events', [])
        
        logger.info(f"   API Number: {api_number}")
        logger.info(f"   Subproject ID: {subproject_id}")
        logger.info(f"   Events: {len(pna_events)}")
        
        # DEBUG: Log first few events
        for i, evt in enumerate(pna_events[:3]):
            event_type_field = evt.get('event_type', '')
            event_id_field = evt.get('event_id')
            logger.info(f"   Event[{i}]: event_type='{event_type_field}', event_id={event_id_field}")
        
        # ============================================================
        # AUTO-GENERATE W-3A FOR THIS API (using orchestrator)
        # ============================================================
        logger.info("\n" + "=" * 80)
        logger.info("🔍 STEP: Checking for W-3A plan and triggering generation if needed...")
        logger.info("=" * 80)
        auto_w3a_result = None
        w3a_geometry_from_db = None
        try:
            from apps.public_core.services.w3_utils import normalize_api_number
            from apps.public_core.services.w3a_orchestrator import generate_w3a_for_api
            from apps.public_core.services.w3_extraction import get_w3a_geometry_from_database
            from apps.public_core.models import ExtractedDocument
            
            # Normalize the API number
            logger.info(f"📥 Input API number: {api_number}")
            normalized_api = normalize_api_number(api_number)
            logger.info(f"✅ Normalized API: {normalized_api}")
            
            if normalized_api:
                logger.info(f"\n🔎 PHASE 1: Checking for existing W-3A plan in database...")
                
                # FIRST: Try to retrieve existing W-3A plan from database
                logger.info(f"   → Calling get_w3a_geometry_from_database()...")
                w3a_geometry_from_db = get_w3a_geometry_from_database(normalized_api)
                
                if w3a_geometry_from_db:
                    logger.info(f"\n✅ SUCCESS - Found existing W-3A plan in database!")
                    logger.info(f"   Will use this geometry for W3 response")
                else:
                    logger.info(f"\n⚠️  No W-3A plan found in database")
                    logger.info(f"   Proceeding to PHASE 2: Check for RRC extractions...")
                    
                    # SECOND: Check if we have RRC extractions (W-2, W-15, GAU)
                    logger.info(f"\n🔎 PHASE 2: Checking for existing RRC extractions (W-2, W-15, GAU)...")
                    logger.info(f"   Searching for W-2 document with API containing: {normalized_api[-8:]}")
                    
                    w2_exists = ExtractedDocument.objects.filter(
                        api_number__contains=normalized_api[-8:],  # Match last 8 digits
                        document_type="w2"
                    ).exists()
                    
                    if w2_exists:
                        logger.info(f"   ✅ Found existing W-2 extraction")
                        logger.info(f"\n✅ RRC extractions already exist for this API")
                        logger.info(f"   W-3A plan should have been created (but not found in DB)")
                        logger.info(f"   → Skipping W-3A orchestrator call")
                    else:
                        logger.info(f"   ❌ No W-2 extraction found")
                        logger.info(f"\n⚠️  PHASE 3: Triggering full W-3A generation via orchestrator...")
                        logger.info(f"   No RRC extractions exist, need to generate complete W-3A plan")
                        try:
                            logger.info(f"   → Calling generate_w3a_for_api()...")
                            # Call orchestrator with default auto-generation parameters
                            auto_w3a_result = generate_w3a_for_api(
                                api_number=normalized_api,
                                plugs_mode="combined",           # Best practice
                                input_mode="extractions",        # RRC data only
                                merge_threshold_ft=500.0,
                                request=request,
                                confirm_fact_updates=False,       # Don't auto-modify well registry
                                allow_precision_upgrades_only=True,  # Conservative
                                use_gau_override_if_invalid=False
                            )
                            
                            if auto_w3a_result and auto_w3a_result.get("success"):
                                logger.info(f"\n✅ W-3A generation SUCCEEDED")
                                logger.info(f"   Snapshot ID: {auto_w3a_result.get('snapshot_id')}")
                                logger.info(f"   Will include this geometry in W3 response")
                            else:
                                error_msg = auto_w3a_result.get('error') if auto_w3a_result else 'Unknown error'
                                logger.warning(f"\n❌ W-3A generation FAILED: {error_msg}")
                                logger.warning(f"   Continuing with W-3 generation anyway (non-fatal)")
                        except Exception as e:
                            logger.warning(f"\n❌ Exception during W-3A orchestrator call: {e}", exc_info=True)
                            logger.warning(f"   Continuing with W-3 generation anyway (non-fatal)")
                            # Continue anyway - don't block W-3 generation
            else:
                logger.warning(f"❌ Could not normalize API number: {api_number}")
        
        except Exception as e:
            logger.error(f"❌ Unexpected exception in auto-W-3A generation check: {e}", exc_info=True)
            logger.warning(f"   Continuing with W-3 generation anyway (non-fatal)")
            # Continue anyway - don't block W-3 generation
        
        try:
            # Build W-3 form
            result = build_w3_from_pna_payload(
                pna_payload=validated_data,
                request=request
            )
            
            # Save W-3 form to database if generation was successful
            if result.get("success"):
                try:
                    logger.info("\n" + "=" * 80)
                    logger.info("💾 SAVING W-3 FORM TO DATABASE")
                    logger.info("=" * 80)
                    
                    from apps.public_core.models import W3FormORM, WellRegistry
                    
                    # Get or create well registry entry
                    api_number = validated_data.get("api_number", "")
                    well, created = WellRegistry.objects.get_or_create(
                        api14=api_number,
                        defaults={
                            "state": "TX",  # Default - should be determined from data
                            "county": "UNKNOWN",  # Default
                            "operator_name": "UNKNOWN",  # Default
                            "lease_name": validated_data.get("well_name", ""),
                            "well_number": "",
                        }
                    )
                    
                    logger.info(f"   Well: {well.api14} ({'created' if created else 'existing'})")
                    
                    # Create W3FormORM from generated form
                    w3_form_data = result.get("w3_form", {})
                    
                    w3_form = W3FormORM.objects.create(
                        well=well,
                        api_number=api_number,
                        status="draft",  # Initial status
                        form_data=w3_form_data,  # Store full W-3 JSON
                        submitted_by=str(request.user) if request.user else "API",
                        submitted_at=None,  # Not submitted yet
                        rrc_confirmation_number=None,
                    )
                    
                    logger.info(f"   ✅ W3FormORM created: ID={w3_form.id}")
                    logger.info(f"   Status: draft")
                    logger.info(f"   Plugs: {len(w3_form_data.get('plugs', []))}")
                    
                    # Store the form ID in result for reference
                    result["w3_form_id"] = str(w3_form.id)
                    result["w3_form_api"] = w3_form.api_number
                    
                    logger.info("=" * 80)
                    
                except Exception as e:
                    logger.error(f"   ❌ Failed to save W3FormORM: {e}", exc_info=True)
                    logger.warning(f"   Continuing anyway - form data still in response but not persisted")
                    # Don't fail the entire request, just warn
            
            # Validate response structure
            response_serializer = BuildW3FromPNAResponseSerializer(data=result)
            
            if not response_serializer.is_valid():
                logger.error(f"❌ Response validation failed: {response_serializer.errors}")
                return Response(
                    {
                        "success": False,
                        "error": "Internal error: response validation failed",
                        "validation_errors": response_serializer.errors,
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            logger.info("✅ Response validated successfully")
            
            # Add well geometry from W-3A (prioritize database, fallback to newly generated)
            logger.info("\n" + "=" * 80)
            logger.info("📦 PHASE 4: Building final response with W-3A geometry...")
            logger.info("=" * 80)
            
            w3a_geometry_to_include = None
            
            if w3a_geometry_from_db:
                logger.info("✅ PRIORITY 1: Using W-3A geometry from DATABASE")
                logger.info(f"   - Casing strings: {len(w3a_geometry_from_db.get('casing_record', []))}")
                logger.info(f"   - Formation tops: {len(w3a_geometry_from_db.get('formation_tops', []))}")
                logger.info(f"   - Perforations: {len(w3a_geometry_from_db.get('perforations', []))}")
                logger.info(f"   - Operational steps: {len(w3a_geometry_from_db.get('operational_steps', []))}")
                w3a_geometry_to_include = w3a_geometry_from_db
            elif auto_w3a_result and auto_w3a_result.get("success"):
                logger.info("✅ PRIORITY 2: Using W-3A geometry from NEWLY-GENERATED orchestrator result")
                geometry = auto_w3a_result.get("w3a_well_geometry", {})
                logger.info(f"   - Casing strings: {len(geometry.get('casing_record', []))}")
                logger.info(f"   - Formation tops: {len(geometry.get('formation_tops', []))}")
                logger.info(f"   - Perforations: {len(geometry.get('perforations', []))}")
                w3a_geometry_to_include = geometry
            else:
                logger.warning("⚠️  NO W-3A geometry available (neither from DB nor newly-generated)")
                logger.warning("   W3 response will be generated without well geometry data")
            
            if w3a_geometry_to_include:
                logger.info("✅ Adding W-3A geometry to response...")
                result["w3a_well_geometry"] = w3a_geometry_to_include
                
                # Re-validate with well geometry added
                response_serializer = BuildW3FromPNAResponseSerializer(data=result)
                if not response_serializer.is_valid():
                    logger.warning(f"⚠️ Response validation failed after adding geometry: {response_serializer.errors}")
                    logger.warning(f"   Still including geometry (it's informational)")
                    # Still include it even if validation fails (geometry is informational)
                else:
                    logger.info("✅ Response validation successful with geometry included")
            else:
                logger.warning("⚠️  Skipping geometry addition (none available)")
            
            # Return response
            if result.get("success"):
                logger.info("✅ W-3 form generated successfully")
                logger.info("=" * 80 + "\n")
                return Response(
                    response_serializer.validated_data,
                    status=status.HTTP_200_OK
                )
            else:
                logger.warning(f"⚠️ W-3 generation failed: {result.get('error')}")
                logger.info("=" * 80 + "\n")
                return Response(
                    response_serializer.validated_data,
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        except Exception as e:
            logger.error(f"❌ Unexpected error: {e}", exc_info=True)
            logger.info("=" * 80 + "\n")
            
            return Response(
                {
                    "success": False,
                    "error": f"Internal server error: {str(e)}",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class W3HealthCheckView(APIView):
    """
    Health check endpoint for W-3 service.
    
    GET /api/w3/health/
    """
    
    permission_classes = []
    
    def get(self, request, *args, **kwargs):
        """Check service health."""
        return Response(
            {
                "status": "ok",
                "service": "w3-form-generation",
                "version": "1.0.0",
            },
            status=status.HTTP_200_OK
        )



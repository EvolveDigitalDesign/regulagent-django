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
        
        logger.info("=" * 80)
        logger.info("🔵 W-3 API REQUEST RECEIVED")
        logger.info("=" * 80)
        logger.info(f"   Request content-type: {request.content_type}")
        logger.info(f"   Request data keys: {list(request.data.keys())}")
        print(f"Request keys: {list(request.data.keys())}", file=sys.stderr)
        
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
            
            logger.info(f"      Well ID: {well_data.get('well_id')}")
            logger.info(f"      API Number: {well_data.get('api_number')}")
            logger.info(f"      Number of events: {len(events)}")
            
            # Unwrap to flat structure expected by serializer
            data['api_number'] = well_data.get('api_number') or data.get('api_number')
            data['well_name'] = well_data.get('well_name') or data.get('well_name')
            data['dwr_id'] = data.get('dwr_id') or 0  # Will need real DWR ID
            data['pna_events'] = events
            
            logger.info(f"      Unwrapped payload: api_number={data['api_number']}, events={len(events)}")
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
        dwr_id = validated_data.get("dwr_id")
        pna_events = validated_data.get('pna_events', [])
        
        logger.info(f"   API Number: {api_number}")
        logger.info(f"   DWR ID: {dwr_id}")
        logger.info(f"   Events: {len(pna_events)}")
        
        # DEBUG: Log first few events
        for i, evt in enumerate(pna_events[:3]):
            event_type_field = evt.get('event_type', '')
            event_id_field = evt.get('event_id')
            logger.info(f"   Event[{i}]: event_type='{event_type_field}', event_id={event_id_field}")
        
        try:
            # Build W-3 form
            result = build_w3_from_pna_payload(
                pna_payload=validated_data,
                request=request
            )
            
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



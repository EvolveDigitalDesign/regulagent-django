"""
API endpoint for importing NM wells from OCD scraper.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.public_core.services.nm_well_import import import_nm_well, batch_import_nm_wells
from apps.public_core.serializers.well_registry import WellRegistrySerializer

logger = logging.getLogger(__name__)


class NMWellImportView(APIView):
    """
    API endpoint to import NM well data from OCD scraper.

    POST /api/public-core/nm-well-import/
    {
        "api": "30-015-28692",
        "workspace_id": 1,  // optional
        "update_existing": true  // optional, default true
    }

    Returns:
        {
            "status": "created" | "updated" | "exists",
            "well": WellRegistry data,
            "scraped_data": NMWellData dict,
            "errors": []
        }
    """

    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request) -> Response:
        """Import NM well by API number."""
        api = request.data.get("api")
        workspace_id = request.data.get("workspace_id")
        update_existing = request.data.get("update_existing", True)

        if not api:
            return Response(
                {"error": "api parameter is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Import well
            result = import_nm_well(
                api=api,
                workspace_id=workspace_id,
                update_existing=update_existing
            )

            # Serialize well data
            well_data = WellRegistrySerializer(result["well"]).data

            return Response({
                "status": result["status"],
                "well": well_data,
                "scraped_data": result["scraped_data"],
                "errors": result["errors"],
            }, status=status.HTTP_200_OK)

        except ValueError as e:
            logger.error(f"Invalid NM API format: {e}")
            return Response(
                {"error": "invalid_api", "message": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

        except Exception as e:
            logger.error(f"Failed to import NM well: {e}", exc_info=True)
            return Response(
                {"error": "import_failed", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class NMWellBatchImportView(APIView):
    """
    API endpoint to batch import multiple NM wells.

    POST /api/public-core/nm-well-batch-import/
    {
        "apis": ["30-015-28692", "30-015-28693"],
        "workspace_id": 1,  // optional
        "update_existing": true  // optional, default true
    }

    Returns:
        {
            "total": 2,
            "created": 1,
            "updated": 1,
            "exists": 0,
            "failed": 0,
            "results": [...],
            "errors": []
        }
    """

    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request) -> Response:
        """Batch import NM wells by API numbers."""
        api_list = request.data.get("apis", [])
        workspace_id = request.data.get("workspace_id")
        update_existing = request.data.get("update_existing", True)

        if not api_list:
            return Response(
                {"error": "apis parameter is required (list of API numbers)"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not isinstance(api_list, list):
            return Response(
                {"error": "apis must be a list"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Batch import wells
            result = batch_import_nm_wells(
                api_list=api_list,
                workspace_id=workspace_id,
                update_existing=update_existing
            )

            # Serialize well data in results
            for item in result["results"]:
                if "well" in item and item.get("status") != "failed":
                    item["well"] = WellRegistrySerializer(item["well"]).data

            return Response(result, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Failed to batch import NM wells: {e}", exc_info=True)
            return Response(
                {"error": "batch_import_failed", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

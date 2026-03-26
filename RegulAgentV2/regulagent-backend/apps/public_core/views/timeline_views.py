import logging

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.public_core.models import WellRegistry
from apps.public_core.models.well_timeline_event import WellTimelineEvent
from apps.public_core.serializers.timeline_serializer import WellTimelineEventSerializer
from apps.public_core.services.timeline_builder import refresh_timeline

logger = logging.getLogger(__name__)


class WellTimelineView(APIView):
    """
    GET /api/wells/<api14>/timeline/
    Returns chronological timeline events for a well.
    """
    permission_classes = [AllowAny]

    def get(self, request, api14):
        well = WellRegistry.objects.filter(api14=api14).first()
        if not well:
            return Response(
                {"error": f"Well {api14} not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        events = WellTimelineEvent.objects.filter(well=well).order_by("event_date", "created_at")

        # Auto-build timeline if none exists
        if not events.exists():
            from apps.public_core.models import ExtractedDocument
            has_docs = ExtractedDocument.objects.filter(
                well=well, status__in=["success", "partial"]
            ).exists()
            if has_docs:
                refresh_timeline(well)
                events = WellTimelineEvent.objects.filter(well=well).order_by("event_date", "created_at")

        serializer = WellTimelineEventSerializer(events, many=True)
        return Response({
            "api14": api14,
            "total_events": events.count(),
            "events": serializer.data,
        })


class WellTimelineRefreshView(APIView):
    """
    POST /api/wells/<api14>/timeline/refresh/
    Force rebuild the timeline for a well.
    """
    permission_classes = [AllowAny]

    def post(self, request, api14):
        well = WellRegistry.objects.filter(api14=api14).first()
        if not well:
            return Response(
                {"error": f"Well {api14} not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        events = refresh_timeline(well)
        serializer = WellTimelineEventSerializer(events, many=True)
        return Response({
            "api14": api14,
            "total_events": len(events),
            "rebuilt": True,
            "events": serializer.data,
        })

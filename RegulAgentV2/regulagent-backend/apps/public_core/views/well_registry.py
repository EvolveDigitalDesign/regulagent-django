from rest_framework import viewsets, mixins
from rest_framework.permissions import IsAuthenticated

from ..models import WellRegistry
from ..serializers.well_registry import WellRegistrySerializer


class WellRegistryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    API endpoint for querying wells.
    Supports filtering by workspace and workspace active status.

    Query parameters:
    - workspace: Filter by workspace ID
    - workspace_active: Filter by workspace is_active status (true/false)
    """
    permission_classes = [IsAuthenticated]
    serializer_class = WellRegistrySerializer

    def get_queryset(self):
        queryset = WellRegistry.objects.all().select_related('workspace').order_by('id')

        # Filter by workspace if provided
        workspace_id = self.request.query_params.get('workspace')
        if workspace_id:
            queryset = queryset.filter(workspace_id=workspace_id)

        # Filter by workspace is_active status
        workspace_active = self.request.query_params.get('workspace_active')
        if workspace_active is not None:
            if workspace_active.lower() == 'true':
                queryset = queryset.filter(workspace__is_active=True)
            elif workspace_active.lower() == 'false':
                queryset = queryset.filter(workspace__is_active=False)

        return queryset



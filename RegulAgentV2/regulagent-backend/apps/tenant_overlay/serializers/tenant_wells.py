"""
Serializers for tenant-aware well registry endpoints.
"""

from rest_framework import serializers

from apps.public_core.models import WellRegistry
from apps.tenant_overlay.models import WellEngagement


class TenantInteractionSerializer(serializers.Serializer):
    """
    Serializes a tenant's interaction history with a specific well.
    """
    has_interacted = serializers.BooleanField()
    first_interaction_at = serializers.DateTimeField(allow_null=True)
    last_interaction_at = serializers.DateTimeField(allow_null=True)
    last_interaction_type = serializers.CharField(allow_null=True)
    interaction_count = serializers.IntegerField()
    mode = serializers.CharField()
    label = serializers.CharField(allow_blank=True)
    owner_user_email = serializers.EmailField(allow_null=True)
    metadata = serializers.JSONField()


class TenantWellSerializer(serializers.ModelSerializer):
    """
    Well data with tenant-specific interaction history.
    
    Shows public well info (api, location, operator) along with
    the authenticated tenant's interaction history (if any).
    """
    tenant_interaction = serializers.SerializerMethodField()
    
    class Meta:
        model = WellRegistry
        fields = [
            'id',
            'api14',
            'state',
            'county',
            'operator_name',
            'field_name',
            'lease_name',
            'well_number',
            'lat',
            'lon',
            'created_at',
            'updated_at',
            'tenant_interaction'
        ]
    
    def get_tenant_interaction(self, well):
        """
        Get the tenant's interaction history with this well.
        Returns None if no interaction exists.
        """
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return None
        
        # Get tenant_id from request user
        user_tenant = request.user.tenants.first()
        if not user_tenant:
            return None
        
        tenant_id = user_tenant.id
        
        try:
            engagement = WellEngagement.objects.get(tenant_id=tenant_id, well=well)
            return {
                'has_interacted': True,
                'first_interaction_at': engagement.first_interaction_at or engagement.created_at,
                'last_interaction_at': engagement.updated_at,
                'last_interaction_type': engagement.last_interaction_type,
                'interaction_count': engagement.interaction_count,
                'mode': engagement.mode,
                'label': engagement.label,
                'owner_user_email': engagement.owner_user.email if engagement.owner_user else None,
                'metadata': engagement.metadata
            }
        except WellEngagement.DoesNotExist:
            return {
                'has_interacted': False,
                'first_interaction_at': None,
                'last_interaction_at': None,
                'last_interaction_type': None,
                'interaction_count': 0,
                'mode': None,
                'label': '',
                'owner_user_email': None,
                'metadata': {}
            }


class BulkWellRequestSerializer(serializers.Serializer):
    """
    Serializer for bulk well query requests.
    """
    api_numbers = serializers.ListField(
        child=serializers.CharField(max_length=14),
        min_length=1,
        max_length=100,  # Limit bulk queries to 100 wells
        help_text="List of API-14 numbers to query (max 100)"
    )


"""
Engagement tracking service for tenant-well interactions.

Provides centralized function to create/update WellEngagement records
whenever a tenant interacts with a well (plan generation, document upload, chat, etc.)
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime
from uuid import UUID

from django.utils import timezone

from apps.public_core.models import WellRegistry
from apps.tenant_overlay.models import WellEngagement

logger = logging.getLogger(__name__)


def track_well_interaction(
    tenant_id: UUID,
    well: WellRegistry,
    interaction_type: str,
    user=None,
    metadata_update: Optional[Dict[str, Any]] = None,
    mode: Optional[str] = None,
    label: Optional[str] = None
) -> WellEngagement:
    """
    Create or update a WellEngagement record to track tenant-well interactions.
    
    Args:
        tenant_id: UUID of the tenant
        well: WellRegistry instance
        interaction_type: Type of interaction (use WellEngagement.InteractionType choices)
        user: Optional user who performed the interaction
        metadata_update: Optional dict to merge into engagement.metadata
        mode: Optional mode to set (upload/rrc/hybrid)
        label: Optional label to set
    
    Returns:
        WellEngagement instance (created or updated)
    
    Example:
        track_well_interaction(
            tenant_id=tenant.id,
            well=well_instance,
            interaction_type=WellEngagement.InteractionType.W3A_GENERATED,
            user=request.user,
            metadata_update={'plan_id': str(plan_id)}
        )
    """
    try:
        engagement, created = WellEngagement.objects.get_or_create(
            tenant_id=tenant_id,
            well=well,
            defaults={
                'mode': mode or WellEngagement.Mode.HYBRID,
                'label': label or '',
                'owner_user': user,
                'last_interaction_type': interaction_type,
                'interaction_count': 1,
                'first_interaction_at': timezone.now(),
                'metadata': metadata_update or {}
            }
        )
        
        if not created:
            # Update existing engagement
            engagement.last_interaction_type = interaction_type
            engagement.interaction_count += 1
            engagement.updated_at = timezone.now()
            
            # Set first_interaction_at if not set (for legacy records)
            if not engagement.first_interaction_at:
                engagement.first_interaction_at = engagement.created_at
            
            # Merge metadata
            if metadata_update:
                current_metadata = engagement.metadata or {}
                current_metadata.update(metadata_update)
                engagement.metadata = current_metadata
            
            # Update mode/label if provided
            if mode:
                engagement.mode = mode
            if label:
                engagement.label = label
            
            # Update owner_user if provided and not already set
            if user and not engagement.owner_user:
                engagement.owner_user = user
            
            engagement.save()
        
        logger.info(
            f"Tracked {interaction_type} for tenant {tenant_id}, well {well.api14} "
            f"(count: {engagement.interaction_count}, created: {created})"
        )
        
        return engagement
        
    except Exception as e:
        logger.exception(
            f"Failed to track engagement for tenant {tenant_id}, well {well.api14}, "
            f"interaction {interaction_type}: {e}"
        )
        raise


def get_tenant_well_history(tenant_id: UUID, well: WellRegistry) -> Optional[WellEngagement]:
    """
    Get the engagement record for a specific tenant-well pair.
    
    Returns None if no engagement exists (tenant has never interacted with this well).
    """
    try:
        return WellEngagement.objects.get(tenant_id=tenant_id, well=well)
    except WellEngagement.DoesNotExist:
        return None


def get_tenant_engagement_list(tenant_id: UUID):
    """
    Get all wells a tenant has engaged with, ordered by most recent interaction.
    
    Returns QuerySet of WellEngagement records.
    """
    return WellEngagement.objects.filter(
        tenant_id=tenant_id
    ).select_related('well', 'owner_user').order_by('-updated_at')


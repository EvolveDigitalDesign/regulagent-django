"""
Plan version history and revert endpoints.

Allows users to:
- View full modification history
- Revert to a previous version
- Compare versions
"""

import logging
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.shortcuts import get_object_or_404

from apps.assistant.models import ChatThread, PlanModification
from apps.public_core.models import PlanSnapshot
from apps.assistant.serializers import PlanModificationSerializer

logger = logging.getLogger(__name__)


@api_view(['GET'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def get_plan_version_history(request, plan_id):
    """
    Get complete version history for a plan.
    
    GET /api/plans/{plan_id}/versions
    
    Returns:
    {
      "baseline": {...},
      "current_version": 3,
      "versions": [
        {
          "version": 0,
          "snapshot": {...},
          "modification": null
        },
        {
          "version": 1,
          "snapshot": {...},
          "modification": {...}
        },
        ...
      ]
    }
    """
    user_tenant = request.user.tenants.first()
    if not user_tenant:
        return Response(
            {"error": "User not associated with any tenant"},
            status=status.HTTP_403_FORBIDDEN
        )
    
    # Get baseline snapshot
    try:
        baseline = PlanSnapshot.objects.get(
            plan_id=plan_id,
            kind=PlanSnapshot.KIND_BASELINE,
            tenant_id=user_tenant.id
        )
    except PlanSnapshot.DoesNotExist:
        return Response(
            {"error": f"Baseline plan {plan_id} not found for your tenant"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Get version history
    versions = PlanModification.get_version_history(baseline)
    
    # Find current version (latest in active thread)
    current_version = len(versions) - 1  # Default to latest
    active_threads = ChatThread.objects.filter(
        baseline_plan=baseline,
        is_active=True,
        created_by=request.user
    )
    if active_threads.exists():
        thread = active_threads.first()
        if thread.current_plan:
            for idx, (snapshot, _) in enumerate(versions):
                if snapshot.id == thread.current_plan.id:
                    current_version = idx
                    break
    
    # Format response
    version_list = []
    for idx, (snapshot, modification) in enumerate(versions):
        version_list.append({
            'version': idx,
            'snapshot_id': snapshot.id,
            'plan_id': snapshot.plan_id,
            'kind': snapshot.kind,
            'status': snapshot.status,
            'created_at': snapshot.created_at,
            'modification': PlanModificationSerializer(modification).data if modification else None
        })
    
    return Response({
        'baseline_plan_id': baseline.plan_id,
        'baseline_snapshot_id': baseline.id,
        'current_version': current_version,
        'total_versions': len(versions),
        'versions': version_list
    })


@api_view(['POST'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def revert_to_version(request, thread_id):
    """
    Revert a chat thread to a previous plan version.
    
    POST /api/chat/threads/{thread_id}/revert
    {
      "version": 1,  // or "snapshot_id": 123
      "reason": "Reverting to simpler approach"
    }
    
    This updates ChatThread.current_plan to point to the specified snapshot.
    Does NOT delete subsequent versions - they remain in history.
    """
    thread = get_object_or_404(ChatThread, id=thread_id)
    
    # Check edit permission
    if not thread.can_edit(request.user):
        return Response(
            {"error": "Only the thread owner can revert versions"},
            status=status.HTTP_403_FORBIDDEN
        )
    
    # Get target version
    version_num = request.data.get('version')
    snapshot_id = request.data.get('snapshot_id')
    reason = request.data.get('reason', 'User reverted to previous version')
    
    if version_num is not None:
        # Get by version number
        versions = PlanModification.get_version_history(thread.baseline_plan)
        if version_num < 0 or version_num >= len(versions):
            return Response(
                {"error": f"Invalid version number. Must be 0-{len(versions)-1}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        target_snapshot, _ = versions[version_num]
    elif snapshot_id is not None:
        # Get by snapshot ID
        try:
            target_snapshot = PlanSnapshot.objects.get(id=snapshot_id)
        except PlanSnapshot.DoesNotExist:
            return Response(
                {"error": f"Snapshot {snapshot_id} not found"},
                status=status.HTTP_404_NOT_FOUND
            )
    else:
        return Response(
            {"error": "Must provide 'version' or 'snapshot_id'"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Verify snapshot is in this plan's history
    if target_snapshot.plan_id != thread.baseline_plan.plan_id:
        return Response(
            {"error": "Snapshot is not part of this plan's history"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Update thread to point to this version
    old_plan = thread.current_plan
    thread.current_plan = target_snapshot
    thread.save()
    
    logger.info(
        f"ChatThread {thread.id} reverted from snapshot {old_plan.id} to {target_snapshot.id} "
        f"by user {request.user.email}. Reason: {reason}"
    )
    
    return Response({
        'message': 'Successfully reverted to previous version',
        'thread_id': thread.id,
        'previous_snapshot_id': old_plan.id if old_plan else None,
        'current_snapshot_id': target_snapshot.id,
        'current_plan_id': target_snapshot.plan_id,
        'reason': reason
    })


@api_view(['GET'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def compare_plan_versions(request, snapshot_id_1, snapshot_id_2):
    """
    Compare two plan snapshots (show diff).
    
    GET /api/plans/compare/{snapshot_id_1}/{snapshot_id_2}
    
    Returns the diff between two snapshots.
    """
    user_tenant = request.user.tenants.first()
    if not user_tenant:
        return Response(
            {"error": "User not associated with any tenant"},
            status=status.HTTP_403_FORBIDDEN
        )
    
    try:
        snapshot1 = PlanSnapshot.objects.get(id=snapshot_id_1, tenant_id=user_tenant.id)
        snapshot2 = PlanSnapshot.objects.get(id=snapshot_id_2, tenant_id=user_tenant.id)
    except PlanSnapshot.DoesNotExist:
        return Response(
            {"error": "One or both snapshots not found"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Find modification that links these (if exists)
    modification = PlanModification.objects.filter(
        source_snapshot=snapshot1,
        result_snapshot=snapshot2
    ).first()
    
    # Generate detailed diff with JSON patch and visualization data
    from apps.assistant.services.plan_differ import generate_plan_diff, generate_visualization_data
    
    plan_diff = generate_plan_diff(snapshot1.payload, snapshot2.payload)
    viz_data = generate_visualization_data(plan_diff)
    
    # If there's a stored modification, include it
    if modification:
        viz_data['modification'] = PlanModificationSerializer(modification).data
    
    # Add version metadata
    viz_data['snapshot_1'] = {
        'id': snapshot1.id,
        'plan_id': snapshot1.plan_id,
        'kind': snapshot1.kind,
        'status': snapshot1.status,
        'created_at': snapshot1.created_at.isoformat()
    }
    viz_data['snapshot_2'] = {
        'id': snapshot2.id,
        'plan_id': snapshot2.plan_id,
        'kind': snapshot2.kind,
        'status': snapshot2.status,
        'created_at': snapshot2.created_at.isoformat()
    }
    
    logger.info(
        f"Compared snapshots {snapshot_id_1} → {snapshot_id_2}: "
        f"{viz_data['summary']['steps_removed']} removed, "
        f"{viz_data['summary']['steps_added']} added, "
        f"{viz_data['summary']['steps_modified']} modified"
    )
    
    return Response(viz_data)


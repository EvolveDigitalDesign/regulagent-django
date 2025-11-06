"""
Tenant guardrail policy management endpoints.

Allows tenant admins to view and configure their organization's
AI modification guardrails.
"""

import logging
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.tenant_overlay.models import TenantGuardrailPolicy
from apps.assistant.services.guardrails import GLOBAL_BASELINE_POLICY

logger = logging.getLogger(__name__)


class TenantGuardrailPolicyView(APIView):
    """
    Get or update tenant guardrail policy.
    
    GET /api/tenant/settings/guardrails/
    PATCH /api/tenant/settings/guardrails/
    """
    
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """
        Get current tenant's guardrail policy.
        
        Response:
        {
          "id": 1,
          "tenant_id": "uuid",
          "risk_profile": "conservative",
          "require_confirmation_above_risk": 0.3,
          "max_material_delta_percent": 0.2,
          "max_steps_removed": 2,
          "allow_new_violations": false,
          "max_modifications_per_session": 5,
          "allowed_operations": [],
          "blocked_operations": ["replace_cibp"],
          "district_overrides": {},
          "notes": "...",
          "created_at": "...",
          "updated_at": "...",
          "global_baseline": {
            "require_confirmation_above_risk": 0.5,
            "max_material_delta_percent": 0.3,
            ...
          }
        }
        """
        user_tenant = request.user.tenants.first()
        if not user_tenant:
            return Response(
                {"error": "User not associated with any tenant"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get or create policy
        policy = TenantGuardrailPolicy.get_for_tenant(user_tenant.id)
        
        return Response({
            "id": policy.id,
            "tenant_id": str(policy.tenant_id),
            "risk_profile": policy.risk_profile,
            "require_confirmation_above_risk": policy.require_confirmation_above_risk,
            "max_material_delta_percent": policy.max_material_delta_percent,
            "max_steps_removed": policy.max_steps_removed,
            "allow_new_violations": policy.allow_new_violations,
            "allow_violations_with_precedent": policy.allow_violations_with_precedent,
            "max_modifications_per_session": policy.max_modifications_per_session,
            "allowed_operations": policy.allowed_operations,
            "blocked_operations": policy.blocked_operations,
            "district_overrides": policy.district_overrides,
            "notes": policy.notes,
            "created_at": policy.created_at.isoformat(),
            "updated_at": policy.updated_at.isoformat(),
            "global_baseline": GLOBAL_BASELINE_POLICY,
            "is_stricter_than_global": self._is_stricter(policy),
        })
    
    def patch(self, request):
        """
        Update tenant's guardrail policy.
        
        PATCH /api/tenant/settings/guardrails/
        {
          "risk_profile": "conservative",
          "require_confirmation_above_risk": 0.25,
          "blocked_operations": ["replace_cibp"],
          "district_overrides": {
            "08A": {"max_material_delta_percent": 0.15}
          }
        }
        
        Note: Changes are validated against global baseline.
        Cannot set values looser than global limits.
        """
        user_tenant = request.user.tenants.first()
        if not user_tenant:
            return Response(
                {"error": "User not associated with any tenant"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        policy = TenantGuardrailPolicy.get_for_tenant(user_tenant.id)
        
        # Update fields
        updatable_fields = [
            'risk_profile',
            'require_confirmation_above_risk',
            'max_material_delta_percent',
            'max_steps_removed',
            'allow_new_violations',
            'allow_violations_with_precedent',
            'max_modifications_per_session',
            'allowed_operations',
            'blocked_operations',
            'district_overrides',
            'notes',
        ]
        
        # Check if custom values are being set
        custom_value_fields = [
            'require_confirmation_above_risk',
            'max_material_delta_percent',
            'max_steps_removed',
            'allow_new_violations',
            'max_modifications_per_session',
        ]
        has_custom_values = any(field in request.data for field in custom_value_fields)
        auto_switched_to_custom = False
        
        # If custom values provided with a preset profile, auto-switch to custom
        if has_custom_values and request.data.get('risk_profile') in ['conservative', 'balanced', 'aggressive']:
            logger.info(
                f"Custom values provided with preset profile '{request.data.get('risk_profile')}' "
                f"- auto-switching to 'custom' profile"
            )
            request.data['risk_profile'] = 'custom'
            auto_switched_to_custom = True
        
        for field in updatable_fields:
            if field in request.data:
                setattr(policy, field, request.data[field])
        
        # Track who made the change
        policy.created_by = request.user
        
        try:
            policy.save()  # Validation happens here
            
            logger.info(
                f"User {request.user.email} updated guardrail policy for tenant {user_tenant.id}"
            )
            
            warnings = self._get_warnings(policy)
            
            # Add note if auto-switched to custom
            if auto_switched_to_custom:
                warnings.insert(0, "Auto-switched to 'custom' profile because you provided specific values")
            
            return Response({
                "message": "Guardrail policy updated successfully",
                "policy": {
                    "risk_profile": policy.risk_profile,
                    "require_confirmation_above_risk": policy.require_confirmation_above_risk,
                    "max_material_delta_percent": policy.max_material_delta_percent,
                    "max_steps_removed": policy.max_steps_removed,
                },
                "warnings": warnings
            })
        
        except ValueError as e:
            logger.warning(
                f"User {request.user.email} attempted invalid policy update: {e}"
            )
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    def _is_stricter(self, policy):
        """Check if policy is stricter than global baseline."""
        return (
            policy.require_confirmation_above_risk < GLOBAL_BASELINE_POLICY['require_confirmation_above_risk'] or
            policy.max_material_delta_percent < GLOBAL_BASELINE_POLICY['max_material_delta_percent']
        )
    
    def _get_warnings(self, policy):
        """Get warnings about policy configuration."""
        warnings = []
        
        if policy.risk_profile == 'conservative':
            warnings.append("Conservative profile may require more manual approvals")
        
        if policy.blocked_operations:
            warnings.append(f"Blocked operations: {', '.join(policy.blocked_operations)}")
        
        if policy.district_overrides:
            warnings.append(f"District-specific overrides active for: {', '.join(policy.district_overrides.keys())}")
        
        return warnings


@api_view(['GET'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def get_risk_profiles(request):
    """
    Get available risk profiles and their configurations.
    
    GET /api/tenant/settings/guardrails/risk-profiles/
    
    Response:
    {
      "profiles": [
        {
          "id": "conservative",
          "name": "Conservative",
          "description": "Strict limits, manual review required",
          "settings": {
            "require_confirmation_above_risk": 0.3,
            "max_material_delta_percent": 0.2,
            ...
          }
        },
        ...
      ],
      "global_baseline": {...}
    }
    """
    profiles = [
        {
            "id": TenantGuardrailPolicy.PROFILE_CONSERVATIVE,
            "name": "Conservative",
            "description": "Strict limits, manual review required for most changes",
            "icon": "🔒",
            "settings": {
                "require_confirmation_above_risk": 0.3,
                "max_material_delta_percent": 0.2,
                "max_steps_removed": 2,
                "max_modifications_per_session": 5,
            },
            "best_for": ["New teams", "High-risk wells", "Regulatory-sensitive areas"]
        },
        {
            "id": TenantGuardrailPolicy.PROFILE_BALANCED,
            "name": "Balanced",
            "description": "Standard limits based on industry best practices",
            "icon": "⚖️",
            "settings": {
                "require_confirmation_above_risk": 0.5,
                "max_material_delta_percent": 0.3,
                "max_steps_removed": 3,
                "max_modifications_per_session": 10,
            },
            "best_for": ["Most organizations", "Standard operations", "Balanced approach"]
        },
        {
            "id": TenantGuardrailPolicy.PROFILE_CUSTOM,
            "name": "Custom",
            "description": "Manually configured settings",
            "icon": "⚙️",
            "settings": None,
            "best_for": ["Advanced users", "Specific requirements", "Multiple districts"]
        },
    ]
    
    return Response({
        "profiles": profiles,
        "global_baseline": GLOBAL_BASELINE_POLICY,
        "note": "All profiles must respect global baseline limits"
    })


@api_view(['GET'])
@authentication_classes([JWTAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def validate_policy_change(request):
    """
    Validate a proposed policy change without saving.
    
    GET /api/tenant/settings/guardrails/validate/?risk_threshold=0.8&material_delta=0.4
    
    Response:
    {
      "valid": false,
      "errors": [
        "Risk threshold 0.8 exceeds global baseline 0.5",
        "Material delta 0.4 exceeds global baseline 0.3"
      ],
      "warnings": []
    }
    """
    errors = []
    warnings = []
    
    # Get proposed values
    risk_threshold = request.query_params.get('risk_threshold')
    material_delta = request.query_params.get('material_delta')
    
    # Validate against global baseline
    if risk_threshold:
        risk_threshold = float(risk_threshold)
        if risk_threshold > GLOBAL_BASELINE_POLICY['require_confirmation_above_risk']:
            errors.append(
                f"Risk threshold {risk_threshold} exceeds global baseline "
                f"{GLOBAL_BASELINE_POLICY['require_confirmation_above_risk']}"
            )
        elif risk_threshold < 0.2:
            warnings.append("Very low risk threshold may require frequent manual approvals")
    
    if material_delta:
        material_delta = float(material_delta)
        if material_delta > GLOBAL_BASELINE_POLICY['max_material_delta_percent']:
            errors.append(
                f"Material delta {material_delta} exceeds global baseline "
                f"{GLOBAL_BASELINE_POLICY['max_material_delta_percent']}"
            )
    
    return Response({
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings
    })


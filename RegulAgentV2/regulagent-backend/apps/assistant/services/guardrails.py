"""
AI tool execution guardrails and safety checks.

Three-tiered enforcement model:
1. Global/Platform Guardrails - Non-negotiable baseline
2. Tenant Overlay Policy - Org-specific risk appetite (can only be stricter)
3. Session Authorization - User-level flags (allow_plan_changes)

Tenant variance is SIGNAL for learning:
- Embeddings include tenant policy context
- AI learns which patterns work for different risk profiles
- System gets smarter by understanding risk appetite impact
"""

import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from uuid import UUID

logger = logging.getLogger(__name__)


# Global baseline policy (non-negotiable platform minimums)
GLOBAL_BASELINE_POLICY = {
    'require_confirmation_above_risk': 0.5,  # Can be lowered by tenant, not raised
    'max_material_delta_percent': 0.3,       # Can be lowered, not raised
    'max_steps_removed': 3,                   # Can be lowered, not raised
    'allow_new_violations': False,            # Cannot be overridden to True
    'max_modifications_per_session': 10,      # Can be lowered, not raised
}


@dataclass
class GuardrailPolicy:
    """
    Tenant-configurable policy for AI tool execution.
    """
    # Plan modification controls
    allow_plan_changes: bool = True  # Can AI modify plans at all?
    require_confirmation_above_risk: float = 0.5  # Auto-confirm only if risk < this
    allowed_operations: List[str] = None  # None = all allowed, or whitelist
    blocked_operations: List[str] = None  # Blacklist specific operations
    
    # Material change limits
    max_material_delta_percent: float = 0.3  # Max 30% material change
    max_steps_removed: int = 3  # Max number of steps AI can remove
    
    # Violation policy
    allow_new_violations: bool = False  # Block if new violations introduced
    
    # Execution limits
    max_modifications_per_session: int = 10  # Safety limit per chat session
    
    def __post_init__(self):
        if self.allowed_operations is None:
            self.allowed_operations = [
                'combine_plugs',
                'adjust_interval',
                'change_materials',
                'replace_cibp',
            ]
        if self.blocked_operations is None:
            self.blocked_operations = []


class GuardrailViolation(Exception):
    """Raised when a tool execution violates guardrails."""
    def __init__(self, message: str, violation_type: str):
        self.violation_type = violation_type
        super().__init__(message)


class ToolExecutionGuardrail:
    """
    Validates tool executions against tenant policy before allowing them.
    """
    
    def __init__(self, policy: GuardrailPolicy = None):
        self.policy = policy or GuardrailPolicy()
    
    def validate_tool_call(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Validate a tool call before execution.
        
        Args:
            tool_name: Name of the tool being called
            tool_args: Arguments passed to the tool
            context: Execution context (user, thread, etc.)
        
        Returns:
            {
                'allowed': bool,
                'reason': str,
                'requires_confirmation': bool,
                'warnings': List[str]
            }
        
        Raises:
            GuardrailViolation: If tool execution is blocked
        """
        warnings = []
        requires_confirmation = False
        
        # 1. Check if plan changes are allowed at all
        if self._is_plan_modification_tool(tool_name):
            if not self.policy.allow_plan_changes:
                raise GuardrailViolation(
                    "Plan modifications are disabled by policy",
                    violation_type="plan_changes_disabled"
                )
            
            if not context.get('user_allow_plan_changes', False):
                raise GuardrailViolation(
                    "User did not authorize plan changes (allow_plan_changes=false)",
                    violation_type="user_authorization_required"
                )
        
        # 2. Check operation whitelist/blacklist
        if tool_name in self.policy.blocked_operations:
            raise GuardrailViolation(
                f"Operation '{tool_name}' is blocked by policy",
                violation_type="operation_blocked"
            )
        
        if (self.policy.allowed_operations and 
            tool_name not in self.policy.allowed_operations and
            self._is_plan_modification_tool(tool_name)):
            raise GuardrailViolation(
                f"Operation '{tool_name}' is not in allowed operations list",
                violation_type="operation_not_allowed"
            )
        
        # 3. Check session modification limit
        session_mod_count = context.get('modifications_this_session', 0)
        if session_mod_count >= self.policy.max_modifications_per_session:
            raise GuardrailViolation(
                f"Maximum modifications per session ({self.policy.max_modifications_per_session}) reached",
                violation_type="session_limit_exceeded"
            )
        
        # 4. Validate specific tool arguments
        if tool_name == 'combine_plugs':
            step_count = len(tool_args.get('step_ids', []))
            if step_count > self.policy.max_steps_removed:
                warnings.append(
                    f"Combining {step_count} steps (limit: {self.policy.max_steps_removed})"
                )
                requires_confirmation = True
        
        # 5. Check predicted risk (if available from context)
        predicted_risk = context.get('predicted_risk_score', 0.0)
        if predicted_risk >= self.policy.require_confirmation_above_risk:
            requires_confirmation = True
            warnings.append(
                f"High risk score ({predicted_risk:.2f}) - confirmation required"
            )
        
        return {
            'allowed': True,
            'reason': 'Passed all guardrail checks',
            'requires_confirmation': requires_confirmation,
            'warnings': warnings
        }
    
    def validate_modification_result(
        self,
        modification_result: Dict[str, Any],
        baseline_payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Validate modification result after execution but before applying.
        
        This is the FINAL check before a modification is applied to the plan.
        
        Args:
            modification_result: Result from plan editor service
            baseline_payload: Original plan payload for comparison
        
        Returns:
            {
                'allowed': bool,
                'reason': str,
                'requires_confirmation': bool,
                'violations': List[str]
            }
        
        Raises:
            GuardrailViolation: If modification violates policy
        """
        violations = []
        requires_confirmation = False
        
        # 1. Check for new violations
        new_violations = modification_result.get('violations_delta', [])
        if new_violations and not self.policy.allow_new_violations:
            raise GuardrailViolation(
                f"Modification would introduce {len(new_violations)} new violation(s): {new_violations}",
                violation_type="new_violations_introduced"
            )
        
        # 2. Check material delta
        baseline_sacks = baseline_payload.get('materials_totals', {}).get('total_sacks', 0)
        modified_sacks = modification_result.get('modified_payload', {}).get('materials_totals', {}).get('total_sacks', 0)
        
        if baseline_sacks > 0:
            material_delta_percent = abs(modified_sacks - baseline_sacks) / baseline_sacks
            if material_delta_percent > self.policy.max_material_delta_percent:
                violations.append(
                    f"Material change ({material_delta_percent:.1%}) exceeds limit "
                    f"({self.policy.max_material_delta_percent:.1%})"
                )
                requires_confirmation = True
        
        # 3. Check risk score
        risk_score = modification_result.get('risk_score', 0.0)
        if risk_score >= self.policy.require_confirmation_above_risk:
            requires_confirmation = True
            violations.append(f"Risk score {risk_score:.2f} requires confirmation")
        
        if violations:
            return {
                'allowed': False,
                'reason': '; '.join(violations),
                'requires_confirmation': True,
                'violations': violations
            }
        
        return {
            'allowed': True,
            'reason': 'Modification passed all safety checks',
            'requires_confirmation': requires_confirmation,
            'violations': []
        }
    
    @staticmethod
    def _is_plan_modification_tool(tool_name: str) -> bool:
        """Check if tool modifies the plan."""
        modification_tools = {
            'combine_plugs',
            'replace_cibp',
            'adjust_interval',
            'change_materials',
            'add_step',
            'remove_step',
            'reorder_steps',
        }
        return tool_name in modification_tools
    
    @classmethod
    def get_tenant_policy(cls, tenant_id: Optional[UUID] = None, district: str = None) -> 'GuardrailPolicy':
        """
        Get guardrail policy for a tenant with three-tier enforcement:
        
        1. Start with global baseline (non-negotiable)
        2. Apply tenant overlay (can only be stricter)
        3. Apply district overrides (if specified)
        
        Args:
            tenant_id: Tenant UUID
            district: Optional district for district-specific overrides
        
        Returns:
            GuardrailPolicy with effective settings
        """
        if not tenant_id:
            # No tenant specified, use global baseline
            return GuardrailPolicy(
                allow_plan_changes=GLOBAL_BASELINE_POLICY.get('allow_plan_changes', True),
                require_confirmation_above_risk=GLOBAL_BASELINE_POLICY['require_confirmation_above_risk'],
                max_material_delta_percent=GLOBAL_BASELINE_POLICY['max_material_delta_percent'],
                max_steps_removed=GLOBAL_BASELINE_POLICY['max_steps_removed'],
                allow_new_violations=GLOBAL_BASELINE_POLICY['allow_new_violations'],
                max_modifications_per_session=GLOBAL_BASELINE_POLICY['max_modifications_per_session'],
            )
        
        try:
            from apps.tenant_overlay.models.tenant_guardrail_policy import TenantGuardrailPolicy
            
            # Get tenant policy (creates default if doesn't exist)
            tenant_policy = TenantGuardrailPolicy.get_for_tenant(tenant_id)
            
            # Get effective policy with district overrides
            effective_config = tenant_policy.get_effective_policy(district=district)
            
            # Build GuardrailPolicy dataclass
            return GuardrailPolicy(
                allow_plan_changes=effective_config['allow_plan_changes'],
                require_confirmation_above_risk=effective_config['require_confirmation_above_risk'],
                max_material_delta_percent=effective_config['max_material_delta_percent'],
                max_steps_removed=effective_config['max_steps_removed'],
                allow_new_violations=effective_config['allow_new_violations'],
                max_modifications_per_session=effective_config['max_modifications_per_session'],
                allowed_operations=effective_config.get('allowed_operations'),
                blocked_operations=effective_config.get('blocked_operations'),
            )
        
        except Exception as e:
            logger.warning(f"Failed to load tenant policy for {tenant_id}: {e}. Using global baseline.")
            # Fallback to global baseline
            return GuardrailPolicy(
                require_confirmation_above_risk=GLOBAL_BASELINE_POLICY['require_confirmation_above_risk'],
                max_material_delta_percent=GLOBAL_BASELINE_POLICY['max_material_delta_percent'],
                max_steps_removed=GLOBAL_BASELINE_POLICY['max_steps_removed'],
                allow_new_violations=GLOBAL_BASELINE_POLICY['allow_new_violations'],
                max_modifications_per_session=GLOBAL_BASELINE_POLICY['max_modifications_per_session'],
            )


def enforce_guardrails(
    tool_name: str,
    tool_args: Dict[str, Any],
    context: Dict[str, Any],
    tenant_id: str = None
) -> Dict[str, Any]:
    """
    Convenience function to enforce guardrails on a tool call.
    
    Usage:
        result = enforce_guardrails(
            tool_name='combine_plugs',
            tool_args={'step_ids': [5, 11]},
            context={
                'user_allow_plan_changes': True,
                'modifications_this_session': 2,
                'predicted_risk_score': 0.15
            },
            tenant_id='uuid-here'
        )
        
        if not result['allowed']:
            return error_response(result['reason'])
        
        if result['requires_confirmation']:
            return confirmation_prompt(result['warnings'])
    
    Returns:
        Validation result dict
    
    Raises:
        GuardrailViolation: If tool execution is blocked
    """
    policy = ToolExecutionGuardrail.get_tenant_policy(tenant_id)
    guardrail = ToolExecutionGuardrail(policy)
    
    return guardrail.validate_tool_call(tool_name, tool_args, context)


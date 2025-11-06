"""
Tenant-specific guardrail policies for AI plan modifications.

These policies layer ON TOP of global platform guardrails, allowing tenants to:
- Set stricter limits (lower risk thresholds, tighter material constraints)
- Define allowed operations
- Configure risk tolerance

Key principle: Tenant policies can only be STRICTER than global defaults, never looser.
This maintains platform safety while respecting organizational risk appetite.
"""

from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class TenantGuardrailPolicy(models.Model):
    """
    Tenant-specific guardrail configuration.
    
    Inheritance model:
    - inherits_from_global=True: Tenant policy overlays on global baseline
    - inherits_from_global=False: Fully custom (still validated against global minimums)
    
    Risk profiles:
    - Conservative: risk_threshold=0.3, max_material_delta=0.2
    - Balanced: risk_threshold=0.5, max_material_delta=0.3 (default)
    - Aggressive: risk_threshold=0.7, max_material_delta=0.4
    
    Tenant variance is SIGNAL for learning:
    - Embeddings include tenant policy context
    - AI learns which patterns work for different risk profiles
    - System gets smarter by understanding risk appetite impact
    """
    
    # Risk profile presets
    PROFILE_CONSERVATIVE = 'conservative'
    PROFILE_BALANCED = 'balanced'
    PROFILE_AGGRESSIVE = 'aggressive'
    PROFILE_CUSTOM = 'custom'
    
    PROFILE_CHOICES = [
        (PROFILE_CONSERVATIVE, 'Conservative - Strict limits, manual review required'),
        (PROFILE_BALANCED, 'Balanced - Standard limits (platform default)'),
        (PROFILE_AGGRESSIVE, 'Aggressive - Relaxed limits, trust AI more'),
        (PROFILE_CUSTOM, 'Custom - Manually configured'),
    ]
    
    # Tenant
    tenant_id = models.UUIDField(
        unique=True,
        db_index=True,
        help_text="Tenant this policy applies to"
    )
    
    # Profile
    risk_profile = models.CharField(
        max_length=16,
        choices=PROFILE_CHOICES,
        default=PROFILE_BALANCED,
        help_text="Predefined risk profile or custom configuration"
    )
    
    # Inheritance
    inherits_from_global = models.BooleanField(
        default=True,
        help_text="If True, overlays on global baseline. If False, uses custom config."
    )
    
    # Core controls
    allow_plan_changes = models.BooleanField(
        default=True,
        help_text="Master switch: can AI modify plans at all?"
    )
    
    require_confirmation_above_risk = models.FloatField(
        default=0.5,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Auto-confirm only if risk < this value (0.0-1.0)"
    )
    
    # Material constraints
    max_material_delta_percent = models.FloatField(
        default=0.3,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Max material change as fraction (0.3 = 30%)"
    )
    
    max_steps_removed = models.IntegerField(
        default=3,
        validators=[MinValueValidator(0)],
        help_text="Max number of steps AI can remove in one modification"
    )
    
    # Violation policy
    allow_new_violations = models.BooleanField(
        default=False,
        help_text="Can AI introduce new compliance violations?"
    )
    
    allow_violations_with_precedent = models.BooleanField(
        default=False,
        help_text="Allow violations if similar precedent exists with RRC approval"
    )
    
    # Session limits
    max_modifications_per_session = models.IntegerField(
        default=10,
        validators=[MinValueValidator(1)],
        help_text="Max modifications per chat session (per hour)"
    )
    
    # Operation controls (JSONField for flexibility)
    allowed_operations = models.JSONField(
        default=list,
        blank=True,
        help_text="Whitelist of allowed operations. Empty = all allowed. Example: ['combine_plugs', 'adjust_interval']"
    )
    
    blocked_operations = models.JSONField(
        default=list,
        blank=True,
        help_text="Blacklist of blocked operations. Example: ['replace_cibp']"
    )
    
    # District-specific overrides
    district_overrides = models.JSONField(
        default=dict,
        blank=True,
        help_text="District-specific policy overrides. Example: {'08A': {'max_material_delta_percent': 0.2}}"
    )
    
    # Metadata
    notes = models.TextField(
        blank=True,
        help_text="Internal notes about this policy configuration"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        'tenants.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_guardrail_policies'
    )
    
    class Meta:
        db_table = 'tenant_overlay_guardrail_policies'
        verbose_name = 'Tenant Guardrail Policy'
        verbose_name_plural = 'Tenant Guardrail Policies'
    
    def __str__(self):
        return f"GuardrailPolicy<{self.tenant_id}>: {self.risk_profile}"
    
    def get_effective_policy(self, district: str = None) -> dict:
        """
        Get the effective policy for a given district.
        Applies district overrides if present.
        
        Returns dict with all policy values.
        """
        policy = {
            'allow_plan_changes': self.allow_plan_changes,
            'require_confirmation_above_risk': self.require_confirmation_above_risk,
            'max_material_delta_percent': self.max_material_delta_percent,
            'max_steps_removed': self.max_steps_removed,
            'allow_new_violations': self.allow_new_violations,
            'allow_violations_with_precedent': self.allow_violations_with_precedent,
            'max_modifications_per_session': self.max_modifications_per_session,
            'allowed_operations': self.allowed_operations,
            'blocked_operations': self.blocked_operations,
        }
        
        # Apply district overrides
        if district and district in self.district_overrides:
            policy.update(self.district_overrides[district])
        
        return policy
    
    def validate_against_global_baseline(self):
        """
        Ensure tenant policy is not looser than global baseline.
        
        Validation rules:
        - risk threshold: can be lower (stricter), not higher
        - material delta: can be lower (stricter), not higher
        - violations: can disallow, cannot force-allow
        
        Raises ValueError if policy violates global baseline.
        """
        from apps.assistant.services.guardrails import GLOBAL_BASELINE_POLICY
        
        errors = []
        
        # Risk threshold cannot be higher than global
        if self.require_confirmation_above_risk > GLOBAL_BASELINE_POLICY['require_confirmation_above_risk']:
            errors.append(
                f"Risk threshold {self.require_confirmation_above_risk} exceeds global baseline "
                f"{GLOBAL_BASELINE_POLICY['require_confirmation_above_risk']}"
            )
        
        # Material delta cannot be higher than global
        if self.max_material_delta_percent > GLOBAL_BASELINE_POLICY['max_material_delta_percent']:
            errors.append(
                f"Material delta {self.max_material_delta_percent} exceeds global baseline "
                f"{GLOBAL_BASELINE_POLICY['max_material_delta_percent']}"
            )
        
        # Cannot force-allow violations if global blocks
        if self.allow_new_violations and not GLOBAL_BASELINE_POLICY['allow_new_violations']:
            errors.append("Cannot allow new violations when global baseline blocks them")
        
        if errors:
            raise ValueError(f"Tenant policy violates global baseline: {'; '.join(errors)}")
    
    def save(self, *args, **kwargs):
        """Override save to apply profile presets and validate."""
        # Apply profile presets
        if self.risk_profile != self.PROFILE_CUSTOM:
            self._apply_profile_preset()
        
        # Validate against global baseline
        if self.inherits_from_global:
            self.validate_against_global_baseline()
        
        super().save(*args, **kwargs)
    
    def _apply_profile_preset(self):
        """Apply preset values based on risk profile."""
        presets = {
            self.PROFILE_CONSERVATIVE: {
                'require_confirmation_above_risk': 0.3,
                'max_material_delta_percent': 0.2,
                'max_steps_removed': 2,
                'allow_new_violations': False,
                'max_modifications_per_session': 5,
            },
            self.PROFILE_BALANCED: {
                'require_confirmation_above_risk': 0.5,
                'max_material_delta_percent': 0.3,
                'max_steps_removed': 3,
                'allow_new_violations': False,
                'max_modifications_per_session': 10,
            },
            self.PROFILE_AGGRESSIVE: {
                'require_confirmation_above_risk': 0.7,
                'max_material_delta_percent': 0.4,
                'max_steps_removed': 5,
                'allow_new_violations': False,  # Still respect global
                'max_modifications_per_session': 20,
            },
        }
        
        if self.risk_profile in presets:
            for key, value in presets[self.risk_profile].items():
                setattr(self, key, value)
    
    @classmethod
    def get_for_tenant(cls, tenant_id):
        """
        Get policy for tenant, or create default if none exists.
        """
        policy, created = cls.objects.get_or_create(
            tenant_id=tenant_id,
            defaults={
                'risk_profile': cls.PROFILE_BALANCED,
                'inherits_from_global': True,
            }
        )
        return policy
    
    def to_metadata_dict(self) -> dict:
        """
        Convert to dict for embedding metadata.
        
        This allows AI to learn patterns by tenant risk profile.
        """
        return {
            'risk_profile': self.risk_profile,
            'risk_threshold': self.require_confirmation_above_risk,
            'max_material_delta': self.max_material_delta_percent,
            'allow_new_violations': self.allow_new_violations,
            'max_steps_removed': self.max_steps_removed,
        }


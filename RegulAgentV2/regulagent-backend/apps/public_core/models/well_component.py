import uuid

from django.db import models


class WellComponent(models.Model):
    """
    Tracks every physical component in a well across four data layers:
    - public: From regulator docs (W-2, C-105, W-15)
    - tenant: Customer addition/override
    - plan_proposed: Written by kernel when plan is generated
    - execution_actual: Written by post-plugging workflow
    """

    class ComponentType(models.TextChoices):
        CASING = "casing", "Casing"
        TUBING = "tubing", "Tubing"
        LINER = "liner", "Liner"
        CEMENT_PLUG = "cement_plug", "Cement Plug"
        BRIDGE_PLUG = "bridge_plug", "Bridge Plug"
        PACKER = "packer", "Packer"
        RETAINER = "retainer", "Retainer"
        PERFORATION = "perforation", "Perforation"
        CEMENT_JOB = "cement_job", "Cement Job"
        FORMATION_TOP = "formation_top", "Formation Top"
        DV_TOOL = "dv_tool", "DV Tool"
        STRADDLE_PACKER = "straddle_packer", "Straddle Packer"

    class Layer(models.TextChoices):
        PUBLIC = "public", "Public"
        TENANT = "tenant", "Tenant"
        PLAN_PROPOSED = "plan_proposed", "Plan Proposed"
        EXECUTION_ACTUAL = "execution_actual", "Execution Actual"

    class LifecycleState(models.TextChoices):
        INSTALLED = "installed", "Installed"
        PROPOSED_ADDITION = "proposed_addition", "Proposed Addition"
        PROPOSED_REMOVAL = "proposed_removal", "Proposed Removal"
        REMOVED = "removed", "Removed"
        MODIFIED = "modified", "Modified"

    # Identity
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    well = models.ForeignKey(
        "public_core.WellRegistry",
        on_delete=models.CASCADE,
        related_name="components",
    )

    # Classification
    component_type = models.CharField(max_length=32, choices=ComponentType.choices)
    layer = models.CharField(max_length=20, choices=Layer.choices)
    lifecycle_state = models.CharField(
        max_length=20,
        choices=LifecycleState.choices,
        default=LifecycleState.INSTALLED,
    )

    # Tenant scoping (null for public layer)
    tenant_id = models.UUIDField(null=True, blank=True, db_index=True)
    workspace = models.ForeignKey(
        "tenants.ClientWorkspace",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="well_components",
    )

    # Plan/execution links
    plan_snapshot = models.ForeignKey(
        "public_core.PlanSnapshot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proposed_components",
    )
    wizard_session = models.ForeignKey(
        "public_core.W3WizardSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="execution_components",
    )

    # Lineage
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="superseded_by",
    )

    # Geometry
    top_ft = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    bottom_ft = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    depth_ft = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    outside_dia_in = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    inside_dia_in = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    hole_size_in = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    weight_ppf = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    grade = models.CharField(max_length=32, blank=True, default="")
    thread_type = models.CharField(max_length=64, blank=True, default="")

    # Cement-specific
    cement_top_ft = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    cement_class = models.CharField(max_length=16, blank=True, default="")
    sacks = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    # Provenance
    source_document_type = models.CharField(max_length=32, blank=True, default="")
    provenance = models.JSONField(default=dict, blank=True)
    confidence = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    as_of = models.DateTimeField(null=True, blank=True)

    # Extensible properties (shot_density, phase_deg, formation name, etc.)
    properties = models.JSONField(default=dict, blank=True)

    # Display
    sort_order = models.IntegerField(default=0)

    # Soft delete for tenant deactivation
    is_archived = models.BooleanField(default=False, db_index=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "public_core_well_component"
        ordering = ["well", "sort_order", "top_ft"]
        indexes = [
            models.Index(fields=["well", "layer", "lifecycle_state"]),
            models.Index(fields=["well", "tenant_id", "layer"]),
            models.Index(fields=["plan_snapshot", "component_type"]),
            models.Index(fields=["well", "component_type", "lifecycle_state"]),
            models.Index(fields=["wizard_session"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        depth_range = ""
        if self.top_ft is not None and self.bottom_ft is not None:
            depth_range = f" @ {self.top_ft}-{self.bottom_ft}ft"
        elif self.depth_ft is not None:
            depth_range = f" @ {self.depth_ft}ft"
        return f"{self.get_component_type_display()} ({self.layer}/{self.lifecycle_state}){depth_range}"

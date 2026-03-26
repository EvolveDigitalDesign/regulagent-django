"""
Django ORM models for C-103 form data persistence.

Stores C-103 events, plugs, and complete C-103 forms for historical tracking,
audit trails, and user review of past C-103 submissions.

Models:
- C103EventORM: Individual operational events (actual field operations)
- C103PlugORM: Grouped plugging operations (NOI plan steps)
- C103FormORM: Complete C-103 form submissions (NOI or Subsequent Report)
- DailyWorkRecord: Daily Work Record for subsequent report DWR tracking
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator
from simple_history.models import HistoricalRecords

from .well_registry import WellRegistry


class C103EventORM(models.Model):
    """
    Individual C-103 operational event (actual field operation).

    Represents atomic operations for subsequent reports: Set Cement Plug,
    Squeeze, Tag TOC, Cut Casing, etc.
    Multiple events can belong to a single daily work record.
    """

    EVENT_TYPE_CHOICES = [
        ('set_cement_plug', 'Set Cement Plug'),
        ('set_surface_plug', 'Set Surface Plug'),
        ('set_bridge_plug', 'Set Bridge Plug (CIBP)'),
        ('set_marker', 'Set Marker'),
        ('squeeze', 'Squeeze Operation'),
        ('tag_toc', 'Tag Top of Cement'),
        ('circulate', 'Circulate'),
        ('pump_cement', 'Pump Cement'),
        ('woc', 'Wait on Cement'),
        ('pressure_test', 'Pressure Test'),
        ('pull_tubing', 'Pull Tubing'),
        ('cut_casing', 'Cut Casing'),
    ]

    # Relationship to well
    well = models.ForeignKey(
        WellRegistry,
        on_delete=models.CASCADE,
        related_name='c103_events',
        null=True,
        blank=True,
    )

    # Parent form (optional — events may exist before form is created)
    c103_form = models.ForeignKey(
        'C103FormORM',
        on_delete=models.CASCADE,
        related_name='events',
        null=True,
        blank=True,
    )

    # API tracking for data correlation
    api_number = models.CharField(
        max_length=20,
        db_index=True,
        help_text='8-digit or 10-digit API number (normalized to 8-digit)',
    )

    # Event identification
    event_type = models.CharField(
        max_length=20,
        choices=EVENT_TYPE_CHOICES,
        db_index=True,
    )
    event_date = models.DateField(db_index=True)
    event_start_time = models.TimeField(null=True, blank=True)
    event_end_time = models.TimeField(null=True, blank=True)

    # Depths (in feet)
    depth_top_ft = models.FloatField(null=True, blank=True)
    depth_bottom_ft = models.FloatField(null=True, blank=True)
    tagged_depth_ft = models.FloatField(null=True, blank=True)

    # Materials
    cement_class = models.CharField(
        max_length=1,
        blank=True,
        choices=[
            ('A', 'Class A'),
            ('B', 'Class B'),
            ('C', 'Class C'),
            ('G', 'Class G'),
            ('H', 'Class H'),
        ],
    )
    sacks = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    volume_bbl = models.FloatField(null=True, blank=True)
    pressure_psi = models.FloatField(null=True, blank=True)

    # Event metadata
    plug_number = models.IntegerField(null=True, blank=True)
    raw_event_detail = models.TextField(blank=True)
    casing_string = models.CharField(max_length=50, blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['event_date', 'event_start_time']
        indexes = [
            models.Index(fields=['api_number', 'event_date']),
            models.Index(fields=['well', 'event_date']),
        ]

    def __str__(self):
        return f"{self.get_event_type_display()} - {self.api_number} ({self.event_date})"


class C103PlugORM(models.Model):
    """
    Stores individual plug steps on a C-103 NOI form.

    Each plug represents one row in the C-103 plugging table.
    Mirrors NM NMAC 19.15.25 requirements (flat excess, CIBP cap, etc.)
    """

    OPERATION_TYPE_CHOICES = [
        ('spot', 'Spot'),
        ('squeeze', 'Squeeze'),
        ('circulate', 'Circulate'),
    ]

    HOLE_TYPE_CHOICES = [
        ('cased', 'Cased Hole'),
        ('open', 'Open Hole'),
    ]

    STEP_TYPE_CHOICES = [
        ('cement_plug', 'Cement Plug'),
        ('formation_plug', 'Formation Plug'),
        ('cibp_cap', 'CIBP Cap'),
        ('shoe_plug', 'Shoe Plug'),
        ('surface_plug', 'Surface Plug'),
        ('duqw_plug', 'DUQW Plug'),
        ('fill_plug', 'Fill Plug'),
        ('mechanical_plug', 'Mechanical Plug'),
    ]

    # Parent form
    c103_form = models.ForeignKey(
        'C103FormORM',
        on_delete=models.CASCADE,
        related_name='plugs',
    )

    # Plug identification
    plug_number = models.IntegerField()
    step_type = models.CharField(max_length=20, choices=STEP_TYPE_CHOICES)
    operation_type = models.CharField(max_length=20, choices=OPERATION_TYPE_CHOICES)
    hole_type = models.CharField(max_length=10, choices=HOLE_TYPE_CHOICES)

    # Depths
    top_ft = models.FloatField()
    bottom_ft = models.FloatField()

    # Cement
    cement_class = models.CharField(
        max_length=1,
        blank=True,
        choices=[
            ('A', 'Class A'),
            ('B', 'Class B'),
            ('C', 'Class C'),
            ('G', 'Class G'),
            ('H', 'Class H'),
        ],
    )
    sacks_required = models.FloatField(
        default=0,
        validators=[MinValueValidator(0)],
    )
    inside_sacks = models.FloatField(null=True, blank=True)
    outside_sacks = models.FloatField(null=True, blank=True)
    excess_factor = models.FloatField(default=0.50)

    # Formation (for formation_plug and shoe_plug steps)
    formation_name = models.CharField(max_length=100, blank=True)

    # Requirements
    tag_required = models.BooleanField(default=True)
    wait_hours = models.IntegerField(default=4)

    # Narrative
    procedure_narrative = models.TextField(blank=True)
    regulatory_basis = models.CharField(max_length=200, blank=True)

    # Casing
    casing_size_in = models.FloatField(null=True, blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['plug_number']
        indexes = [
            models.Index(fields=['c103_form', 'plug_number']),
        ]

    def __str__(self):
        return f"C-103 Plug {self.plug_number} - {self.get_step_type_display()}"


class C103FormORM(models.Model):
    """
    Stores complete C-103 form submissions (NOI or Subsequent Report).

    Represents the final generated C-103 form ready for NMOCD submission.
    Links to all events and plugs that compose it.
    """

    FORM_TYPE_CHOICES = [
        ('noi', 'Notice of Intent'),
        ('subsequent', 'Subsequent Report'),
    ]

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('internal_review', 'Internal Review'),
        ('engineer_approved', 'Engineer Approved'),
        ('filed', 'Filed with Agency'),
        ('agency_approved', 'Agency Approved'),
        ('agency_rejected', 'Agency Rejected'),
        ('revision_requested', 'Revision Requested'),
    ]

    LEASE_TYPE_CHOICES = [
        ('state', 'State'),
        ('fee', 'Fee'),
        ('federal', 'Federal'),
        ('indian', 'Indian'),
    ]

    # Relationship to well
    well = models.ForeignKey(
        WellRegistry,
        on_delete=models.CASCADE,
        related_name='c103_forms',
        null=True,
        blank=True,
    )

    # API tracking
    api_number = models.CharField(
        max_length=20,
        db_index=True,
    )

    # Form metadata
    form_type = models.CharField(
        max_length=20,
        choices=FORM_TYPE_CHOICES,
        default='noi',
        db_index=True,
    )
    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default='draft',
        db_index=True,
    )

    # Tenant and workspace isolation (matches W3FormORM / PlanSnapshot pattern)
    tenant_id = models.UUIDField(null=True, blank=True, db_index=True)
    workspace = models.ForeignKey(
        'tenants.ClientWorkspace',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='c103_forms',
        db_index=True,
    )

    # NM-specific fields
    region = models.CharField(max_length=30, blank=True)
    sub_area = models.CharField(max_length=50, blank=True, null=True)
    coa_figure = models.CharField(max_length=5, blank=True)
    lease_type = models.CharField(
        max_length=20,
        choices=LEASE_TYPE_CHOICES,
        blank=True,
    )

    # Plan data (JSON storage for full plan)
    plan_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='Complete C-103 plan JSON (header, plugs, compliance, narrative)',
    )
    proposed_work_narrative = models.TextField(blank=True)

    # Compliance
    compliance_violations = models.JSONField(default=list, blank=True)

    # Linked plan snapshot (if generated via kernel)
    plan_snapshot = models.ForeignKey(
        'PlanSnapshot',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='c103_forms',
    )

    # Submission tracking
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.CharField(max_length=255, null=True, blank=True)
    nmocd_confirmation_number = models.CharField(max_length=255, null=True, blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Django simple-history for audit trail (tracks who/when for all status changes)
    history = HistoricalRecords()

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'C-103 Form'
        verbose_name_plural = 'C-103 Forms'
        indexes = [
            models.Index(fields=['api_number', 'status']),
            models.Index(fields=['well', '-created_at']),
            models.Index(fields=['tenant_id']),
            models.Index(fields=['workspace']),
            models.Index(fields=['tenant_id', 'workspace']),
            models.Index(fields=['api_number', 'form_type']),
        ]

    def __str__(self):
        return f"C-103 {self.get_form_type_display()} - {self.api_number} ({self.get_status_display()})"

    def mark_filed(self, submitted_by: str, nmocd_confirmation_number: str = None):
        """Mark this C-103 as filed with NMOCD."""
        self.status = 'filed'
        self.submitted_at = timezone.now()
        self.submitted_by = submitted_by
        if nmocd_confirmation_number:
            self.nmocd_confirmation_number = nmocd_confirmation_number
        self.save()


class DailyWorkRecord(models.Model):
    """
    Daily Work Record for C-103 subsequent reports.

    Tracks field operations on a per-day basis, linking C103EventORM
    entries to the day they occurred.
    """

    # Parent form
    c103_form = models.ForeignKey(
        C103FormORM,
        on_delete=models.CASCADE,
        related_name='daily_work_records',
    )

    work_date = models.DateField()
    day_number = models.IntegerField(default=1)

    # Events for this day
    events = models.ManyToManyField(
        C103EventORM,
        blank=True,
        related_name='daily_records',
    )

    # Narrative
    daily_narrative = models.TextField(blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['work_date']
        unique_together = [['c103_form', 'work_date']]

    def __str__(self):
        return f"DWR Day {self.day_number} - {self.work_date} ({self.c103_form})"

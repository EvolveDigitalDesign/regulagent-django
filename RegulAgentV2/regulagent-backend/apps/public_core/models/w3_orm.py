"""
Django ORM models for W-3 form data persistence.

Stores W-3 events, plugs, and complete W-3 forms for historical tracking,
audit trails, and user review of past W-3 submissions.

Models:
- W3EventORM: Individual operational events from pnaexchange
- W3PlugORM: Grouped plugging operations
- W3FormORM: Complete W-3 form submissions
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from simple_history.models import HistoricalRecords

from .well_registry import WellRegistry


class W3EventORM(models.Model):
    """
    Stores individual W-3 operational events from pnaexchange.
    
    Represents atomic operations: Set Plug, Squeeze, Perforate, Tag TOC, Cut Casing, etc.
    Multiple events can belong to a single plug operation.
    """
    
    # Event type choices matching pnaexchange event types
    EVENT_TYPE_CHOICES = [
        ('set_cement_plug', 'Set Cement Plug'),
        ('set_surface_plug', 'Set Surface Plug'),
        ('set_bridge_plug', 'Set Bridge Plug (CIBP)'),
        ('squeeze', 'Squeeze Operation'),
        ('perforate', 'Perforation'),
        ('tag_toc', 'Tag Top of Cement'),
        ('tag_bridge_plug', 'Tag Bridge Plug'),
        ('cut_casing', 'Cut Casing'),
        ('broke_circulation', 'Broke Circulation'),
        ('pressure_up', 'Pressure Up'),
        ('rrc_approval', 'RRC Approval'),
        ('other', 'Other Event'),
    ]
    
    # Relationship to well
    well = models.ForeignKey(
        WellRegistry,
        on_delete=models.CASCADE,
        related_name="w3_events",
        null=True,
        blank=True
    )
    
    # API tracking for data correlation
    api_number = models.CharField(
        max_length=14,
        db_index=True,
        help_text="8-digit or 10-digit API number (normalized to 8-digit)"
    )
    
    # Event identification
    event_type = models.CharField(
        max_length=50,
        choices=EVENT_TYPE_CHOICES,
        db_index=True
    )
    event_date = models.DateField(db_index=True)
    event_start_time = models.TimeField(null=True, blank=True)
    event_end_time = models.TimeField(null=True, blank=True)
    
    # Depths (in feet)
    depth_top_ft = models.FloatField(null=True, blank=True)
    depth_bottom_ft = models.FloatField(null=True, blank=True)
    perf_depth_ft = models.FloatField(null=True, blank=True)
    tagged_depth_ft = models.FloatField(null=True, blank=True)
    
    # Materials
    cement_class = models.CharField(
        max_length=1,
        null=True,
        blank=True,
        choices=[
            ('A', 'Class A'),
            ('B', 'Class B'),
            ('C', 'Class C'),
            ('G', 'Class G'),
            ('H', 'Class H'),
        ]
    )
    sacks = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0)]
    )
    volume_bbl = models.FloatField(null=True, blank=True)
    pressure_psi = models.FloatField(null=True, blank=True)
    
    # Event metadata
    plug_number = models.IntegerField(null=True, blank=True)
    raw_event_detail = models.TextField(blank=True)
    work_assignment_id = models.IntegerField(null=True, blank=True)
    dwr_id = models.IntegerField(null=True, blank=True)
    
    # Casing state
    jump_to_next_casing = models.BooleanField(default=False)
    casing_string = models.CharField(max_length=50, null=True, blank=True)
    
    # Raw input tracking
    raw_input_values = models.JSONField(default=dict)
    raw_transformation_rules = models.JSONField(default=dict)
    
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


class W3PlugORM(models.Model):
    """
    Stores grouped plugging operations.
    
    Multiple W3Events can be grouped into a single plug (e.g., perforate + squeeze + tag).
    Each plug represents one row in the W-3 form plugging table.
    """
    
    PLUG_TYPE_CHOICES = [
        ('cement_plug', 'Cement Plug'),
        ('bridge_plug', 'Bridge Plug (CIBP)'),
        ('squeeze', 'Squeeze Operation'),
        ('surface_plug', 'Surface Plug'),
        ('production_plug', 'Production Plug'),
        ('other', 'Other Plug Type'),
    ]
    
    OPERATION_TYPE_CHOICES = [
        ('spot', 'Spot (Inside Casing Only)'),
        ('squeeze', 'Squeeze (Perforate & Squeeze into Annulus)'),
        ('other', 'Other Operation'),
    ]
    
    # Relationship to well
    well = models.ForeignKey(
        WellRegistry,
        on_delete=models.CASCADE,
        related_name="w3_plugs",
        null=True,
        blank=True
    )
    
    # API tracking
    api_number = models.CharField(
        max_length=14,
        db_index=True
    )
    
    # Plug identification
    plug_number = models.IntegerField()
    plug_type = models.CharField(
        max_length=50,
        choices=PLUG_TYPE_CHOICES
    )
    operation_type = models.CharField(
        max_length=50,
        choices=OPERATION_TYPE_CHOICES,
        null=True,
        blank=True
    )
    
    # Events in this plug
    events = models.ManyToManyField(W3EventORM, related_name="plugs")
    
    # Depths
    depth_top_ft = models.FloatField(null=True, blank=True)
    depth_bottom_ft = models.FloatField(null=True, blank=True)
    
    # Materials
    cement_class = models.CharField(
        max_length=1,
        null=True,
        blank=True,
        choices=[
            ('A', 'Class A'),
            ('B', 'Class B'),
            ('C', 'Class C'),
            ('G', 'Class G'),
            ('H', 'Class H'),
        ]
    )
    sacks = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0)]
    )
    volume_bbl = models.FloatField(null=True, blank=True)
    slurry_weight_ppg = models.FloatField(
        default=14.8,
        validators=[MinValueValidator(0)]
    )
    hole_size_in = models.FloatField(null=True, blank=True)
    
    # Top of Cement (TOC) tracking
    calculated_top_of_plug_ft = models.FloatField(null=True, blank=True)
    measured_top_of_plug_ft = models.FloatField(null=True, blank=True)
    toc_variance_ft = models.FloatField(null=True, blank=True)
    
    # Remarks
    remarks = models.TextField(blank=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['plug_number']
        unique_together = [['api_number', 'plug_number']]
        indexes = [
            models.Index(fields=['api_number', 'plug_number']),
            models.Index(fields=['well', 'plug_number']),
        ]
    
    def __str__(self):
        return f"Plug {self.plug_number} - {self.api_number}"


class W3FormORM(models.Model):
    """
    Stores complete W-3 form submissions.
    
    Represents the final generated W-3 form ready for RRC submission.
    Links to all events and plugs that compose it.
    """
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('submitted', 'Submitted to RRC'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('archived', 'Archived'),
    ]
    
    # Relationship to well
    well = models.ForeignKey(
        WellRegistry,
        on_delete=models.CASCADE,
        related_name="w3_forms",
        null=True,
        blank=True
    )
    
    # API tracking
    api_number = models.CharField(
        max_length=14,
        db_index=True
        # Multiple W-3s possible: initial plug, redrill, re-plug, etc.
    )
    
    # Status
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
        db_index=True
    )
    
    # Plugs in this form
    plugs = models.ManyToManyField(W3PlugORM, related_name="w3_forms")
    
    # Form data (complete W-3 structure)
    form_data = models.JSONField(
        help_text="Complete W-3 form JSON (header, plugs, casing_record, perforations, duqw, remarks)"
    )
    
    # Well geometry for diagram
    well_geometry = models.JSONField(
        default=dict,
        help_text="Casing record, existing tools, retainer tools, historic cement, KOP"
    )
    
    # RRC Export (formatted for submission)
    rrc_export = models.JSONField(
        default=list,
        help_text="Array of plug rows formatted for RRC submission"
    )
    
    # Validation
    validation_warnings = models.JSONField(default=list)
    validation_errors = models.JSONField(default=list)
    
    # Submission tracking
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.CharField(max_length=255, null=True, blank=True)
    rrc_confirmation_number = models.CharField(max_length=255, null=True, blank=True)
    
    # Metadata
    generated_from_w3a_snapshot = models.UUIDField(
        null=True,
        blank=True,
        help_text="ID of W-3A plan snapshot used to generate this form"
    )
    
    auto_generated = models.BooleanField(
        default=False,
        help_text="True if W-3A was auto-generated from RRC data"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Django simple-history for audit trail (tracks who/when for all status changes)
    history = HistoricalRecords()
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['api_number', 'status']),
            models.Index(fields=['well', '-created_at']),
        ]
    
    def __str__(self):
        return f"W-3 Form - {self.api_number} ({self.get_status_display()})"
    
    def mark_submitted(self, submitted_by: str, rrc_confirmation_number: str = None):
        """Mark this W-3 as submitted to RRC."""
        self.status = 'submitted'
        self.submitted_at = timezone.now()
        self.submitted_by = submitted_by
        if rrc_confirmation_number:
            self.rrc_confirmation_number = rrc_confirmation_number
        self.save()


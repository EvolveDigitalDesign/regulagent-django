"""
DRF Serializers for C-103 ORM models.

Serializers for C103EventORM, C103PlugORM, and C103FormORM with support for:
- Nested relationships (plugs and events nested in form detail)
- List and detail views
- Create/update operations
- Form submission
"""

from __future__ import annotations

from rest_framework import serializers

from apps.public_core.models import C103EventORM, C103PlugORM, C103FormORM


# ---- C103Event Serializers ----

class C103EventSerializer(serializers.ModelSerializer):
    """Full serializer for C-103 events (used in nested and standalone contexts)."""

    event_type_display = serializers.CharField(source='get_event_type_display', read_only=True)
    cement_class_display = serializers.CharField(source='get_cement_class_display', read_only=True)

    class Meta:
        model = C103EventORM
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class C103EventCreateUpdateSerializer(serializers.ModelSerializer):
    """Create/update serializer for C-103 events — excludes c103_form (set via URL)."""

    class Meta:
        model = C103EventORM
        exclude = ['c103_form']


# ---- C103Plug Serializers ----

class C103PlugSerializer(serializers.ModelSerializer):
    """Full serializer for C-103 plugs (used in nested and standalone contexts)."""

    step_type_display = serializers.CharField(source='get_step_type_display', read_only=True)
    operation_type_display = serializers.CharField(source='get_operation_type_display', read_only=True)
    hole_type_display = serializers.CharField(source='get_hole_type_display', read_only=True)
    cement_class_display = serializers.CharField(source='get_cement_class_display', read_only=True)

    class Meta:
        model = C103PlugORM
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class C103PlugCreateUpdateSerializer(serializers.ModelSerializer):
    """Create/update serializer for C-103 plugs — excludes c103_form (set via URL)."""

    class Meta:
        model = C103PlugORM
        exclude = ['c103_form']


# ---- C103Form Serializers ----

class C103FormListSerializer(serializers.ModelSerializer):
    """Lightweight list view — no plan_data JSON blob."""

    status_display = serializers.CharField(source='get_status_display', read_only=True)
    form_type_display = serializers.CharField(source='get_form_type_display', read_only=True)
    plug_count = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = C103FormORM
        fields = [
            'id',
            'api_number',
            'form_type',
            'form_type_display',
            'status',
            'status_display',
            'region',
            'coa_figure',
            'lease_type',
            'plug_count',
            'submitted_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'submitted_at']

    def get_plug_count(self, obj):
        """Return count of plugs on this form."""
        return obj.plugs.count()


class C103FormDetailSerializer(serializers.ModelSerializer):
    """Full detail view with nested plugs and events."""

    status_display = serializers.CharField(source='get_status_display', read_only=True)
    form_type_display = serializers.CharField(source='get_form_type_display', read_only=True)
    lease_type_display = serializers.CharField(source='get_lease_type_display', read_only=True)
    plugs = C103PlugSerializer(many=True, read_only=True)
    events = C103EventSerializer(many=True, read_only=True)
    well_details = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = C103FormORM
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'submitted_at']

    def get_well_details(self, obj):
        """Return minimal well info."""
        if obj.well:
            return {
                'id': obj.well.id,
                'api_number': obj.well.api_number,
                'well_name': obj.well.well_name,
            }
        return None


class C103FormCreateUpdateSerializer(serializers.ModelSerializer):
    """Create/update — accepts plan_data, proposed_work_narrative."""

    class Meta:
        model = C103FormORM
        fields = [
            'well',
            'api_number',
            'form_type',
            'region',
            'sub_area',
            'coa_figure',
            'lease_type',
            'plan_data',
            'proposed_work_narrative',
        ]


class C103FormSubmitSerializer(serializers.Serializer):
    """Submit form for filing."""

    submitted_by = serializers.CharField(max_length=255)
    nmocd_confirmation_number = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )
    notes = serializers.CharField(required=False, allow_blank=True)


# ---- DWR / Subsequent Report Serializers ----

class DWRUploadSerializer(serializers.Serializer):
    """Multipart file upload for one or more DWR PDFs."""

    files = serializers.ListField(
        child=serializers.FileField(),
        allow_empty=False,
        help_text="One or more DWR PDF files.",
    )

    def validate_files(self, files):
        for f in files:
            if not f.name.lower().endswith(".pdf"):
                raise serializers.ValidationError(
                    f"File '{f.name}' is not a PDF. Only PDF files are accepted."
                )
        return files


class SubsequentReportSerializer(serializers.Serializer):
    """Detail view for a generated subsequent report.

    Used as the response body for upload-dwr (preview) and
    subsequent-report (committed) endpoints.
    """

    api_number = serializers.CharField(read_only=True)
    noi_form_id = serializers.IntegerField(read_only=True)

    # Day-by-day summaries
    daily_summaries = serializers.ListField(
        child=serializers.DictField(),
        read_only=True,
    )

    # As-plugged plug list
    actual_plugs = serializers.ListField(
        child=serializers.DictField(),
        read_only=True,
    )

    # Reconciliation result
    reconciliation = serializers.DictField(read_only=True, allow_null=True)

    # Narrative and metadata
    operations_narrative = serializers.CharField(read_only=True)
    total_days = serializers.IntegerField(read_only=True)
    start_date = serializers.DateField(read_only=True, allow_null=True)
    end_date = serializers.DateField(read_only=True, allow_null=True)

    # Set only when a subsequent C103FormORM has been persisted
    subsequent_form_id = serializers.IntegerField(
        read_only=True, allow_null=True, required=False
    )

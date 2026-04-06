"""
Serializers for bulk operations.
"""
from rest_framework import serializers
from apps.public_core.models import BulkJob


class BulkJobSerializer(serializers.ModelSerializer):
    """
    Serializer for BulkJob model.
    """
    progress_percentage = serializers.FloatField(read_only=True)
    estimated_time_remaining_seconds = serializers.IntegerField(read_only=True)

    class Meta:
        model = BulkJob
        fields = [
            'id',
            'tenant_id',
            'job_type',
            'status',
            'total_items',
            'processed_items',
            'failed_items',
            'progress_percentage',
            'estimated_time_remaining_seconds',
            'input_data',
            'result_data',
            'error_message',
            'created_by',
            'celery_task_id',
            'created_at',
            'started_at',
            'completed_at',
        ]
        read_only_fields = [
            'id',
            'created_at',
            'started_at',
            'completed_at',
            'progress_percentage',
            'estimated_time_remaining_seconds',
        ]


class BulkGeneratePlansRequestSerializer(serializers.Serializer):
    """
    Request serializer for bulk plan generation.
    """
    well_ids = serializers.ListField(
        child=serializers.CharField(max_length=14),
        min_length=1,
        max_length=1000,
        help_text="List of API14 well identifiers"
    )
    options = serializers.DictField(
        required=False,
        default=dict,
        help_text="Optional configuration"
    )

    def validate_well_ids(self, value):
        """Validate well_ids list."""
        if len(value) > 1000:
            raise serializers.ValidationError("Maximum 1000 wells per bulk operation")
        return value


class BulkUpdateStatusRequestSerializer(serializers.Serializer):
    """
    Request serializer for bulk status updates.
    """
    plan_ids = serializers.ListField(
        child=serializers.CharField(max_length=100),
        min_length=1,
        max_length=1000,
        help_text="List of plan_id strings"
    )
    new_status = serializers.CharField(
        max_length=32,
        help_text="Target status for all plans"
    )

    def validate_plan_ids(self, value):
        """Validate plan_ids list."""
        if len(value) > 1000:
            raise serializers.ValidationError("Maximum 1000 plans per bulk operation")
        return value

    def validate_new_status(self, value):
        """Validate status is valid choice."""
        from apps.public_core.models import PlanSnapshot
        valid_statuses = [choice[0] for choice in PlanSnapshot.STATUS_CHOICES]
        if value not in valid_statuses:
            raise serializers.ValidationError(
                f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
            )
        return value

from rest_framework import serializers

from apps.public_core.models.well_timeline_event import WellTimelineEvent


class WellTimelineEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = WellTimelineEvent
        fields = [
            "id",
            "event_date",
            "event_date_precision",
            "event_type",
            "title",
            "description",
            "key_data",
            "source_document_type",
            "source_document",
            "source_segment",
            "created_at",
        ]
        read_only_fields = fields

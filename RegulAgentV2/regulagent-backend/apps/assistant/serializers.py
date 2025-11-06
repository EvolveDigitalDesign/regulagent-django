"""
Serializers for chat and plan modification APIs.
"""

from rest_framework import serializers
from apps.assistant.models import ChatThread, ChatMessage, PlanModification
from apps.public_core.models import PlanSnapshot, WellRegistry


class ChatThreadCreateSerializer(serializers.Serializer):
    """
    Create a new chat thread for a well and plan.
    """
    well_api14 = serializers.CharField(
        max_length=14,
        help_text="14-digit API number for the well"
    )
    plan_id = serializers.CharField(
        max_length=128,
        help_text="Plan ID (e.g., '4200346118:combined')"
    )
    title = serializers.CharField(
        max_length=256,
        required=False,
        allow_blank=True,
        help_text="Optional title for the thread"
    )
    mode = serializers.ChoiceField(
        choices=['assistant', 'tool'],
        default='assistant',
        help_text="Conversation mode"
    )
    share_with_user_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text="Optional list of user IDs to share this thread with (read-only access)"
    )


class ChatThreadSerializer(serializers.ModelSerializer):
    """
    Full chat thread details including sharing information.
    """
    well_api14 = serializers.CharField(source='well.api14', read_only=True)
    well_operator = serializers.CharField(source='well.operator_name', read_only=True)
    well_field = serializers.CharField(source='well.field_name', read_only=True)
    baseline_plan_id = serializers.CharField(source='baseline_plan.plan_id', read_only=True)
    current_plan_id = serializers.CharField(source='current_plan.plan_id', read_only=True, allow_null=True)
    
    created_by_email = serializers.EmailField(source='created_by.email', read_only=True)
    shared_with_emails = serializers.SerializerMethodField()
    
    message_count = serializers.SerializerMethodField()
    modification_count = serializers.SerializerMethodField()
    
    # Permission helpers for frontend
    permissions = serializers.SerializerMethodField()
    
    class Meta:
        model = ChatThread
        fields = [
            'id',
            'tenant_id',
            'created_by_email',
            'shared_with_emails',
            'well_api14',
            'well_operator',
            'well_field',
            'baseline_plan_id',
            'current_plan_id',
            'openai_thread_id',
            'title',
            'mode',
            'is_active',
            'created_at',
            'updated_at',
            'last_message_at',
            'message_count',
            'modification_count',
            'permissions',
        ]
        read_only_fields = [
            'id',
            'tenant_id',
            'openai_thread_id',
            'created_at',
            'updated_at',
            'last_message_at',
        ]
    
    def get_shared_with_emails(self, obj):
        return [user.email for user in obj.shared_with.all()]
    
    def get_message_count(self, obj):
        return obj.messages.count()
    
    def get_modification_count(self, obj):
        return obj.modifications.count()
    
    def get_permissions(self, obj):
        """Return permission info for the requesting user."""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return {'can_view': False, 'can_edit': False}
        
        return {
            'can_view': obj.can_view(request.user),
            'can_edit': obj.can_edit(request.user),
        }


class ChatMessageSerializer(serializers.ModelSerializer):
    """
    Chat message with tool call details.
    """
    class Meta:
        model = ChatMessage
        fields = [
            'id',
            'thread',
            'role',
            'content',
            'tool_calls',
            'tool_results',
            'metadata',
            'created_at',
        ]
        read_only_fields = [
            'id',
            'thread',
            'created_at',
        ]


class ChatMessageCreateSerializer(serializers.Serializer):
    """
    Create a new message in a thread (user input).
    """
    content = serializers.CharField(
        help_text="User message content"
    )
    allow_plan_changes = serializers.BooleanField(
        default=True,
        help_text="Allow assistant to make plan modifications"
    )
    max_tool_calls = serializers.IntegerField(
        default=10,
        min_value=1,
        max_value=20,
        help_text="Maximum number of tool calls per response"
    )


class PlanModificationSerializer(serializers.ModelSerializer):
    """
    Plan modification details with diff and risk assessment.
    """
    source_plan_id = serializers.CharField(source='source_snapshot.plan_id', read_only=True)
    result_plan_id = serializers.CharField(source='result_snapshot.plan_id', read_only=True, allow_null=True)
    applied_by_email = serializers.EmailField(source='applied_by.email', read_only=True, allow_null=True)
    
    class Meta:
        model = PlanModification
        fields = [
            'id',
            'op_type',
            'description',
            'operation_payload',
            'diff',
            'risk_score',
            'violations_delta',
            'source_plan_id',
            'result_plan_id',
            'chat_thread',
            'chat_message',
            'applied_by_email',
            'is_applied',
            'is_reverted',
            'created_at',
            'applied_at',
            'reverted_at',
        ]
        read_only_fields = [
            'id',
            'created_at',
            'applied_at',
            'reverted_at',
        ]


class ChatThreadShareSerializer(serializers.Serializer):
    """
    Share or unshare a thread with users.
    """
    user_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text="List of user IDs to share with or unshare from"
    )
    action = serializers.ChoiceField(
        choices=['add', 'remove'],
        help_text="'add' to share, 'remove' to unshare"
    )


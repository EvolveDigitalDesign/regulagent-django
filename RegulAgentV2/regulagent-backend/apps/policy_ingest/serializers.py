from rest_framework import serializers
from .models import PolicyRule, PolicySection


class PolicyRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = PolicyRule
        fields = (
            'id', 'rule_id', 'citation', 'title', 'source_urls', 'jurisdiction', 'doc_type', 'topic',
            'version_tag', 'effective_from', 'effective_to', 'html_sha256',
            'created_at', 'updated_at'
        )


class PolicySectionSerializer(serializers.ModelSerializer):
    rule = PolicyRuleSerializer(read_only=True)

    class Meta:
        model = PolicySection
        fields = (
            'id', 'rule', 'version_tag', 'path', 'heading', 'text', 'anchor', 'order_idx', 'created_at'
        )



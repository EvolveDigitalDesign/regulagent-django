from __future__ import annotations

from typing import Any, Dict

from rest_framework import serializers


class W3AFromApiRequestSerializer(serializers.Serializer):
    api10 = serializers.CharField(help_text="10-digit API number")
    use_gau_override_if_invalid = serializers.BooleanField(required=False, default=False)
    confirm_fact_updates = serializers.BooleanField(required=False, default=False)
    allow_precision_upgrades_only = serializers.BooleanField(required=False, default=True)
    input_mode = serializers.ChoiceField(
        choices=("extractions", "user_files", "hybrid"), required=False, default="extractions"
    )
    plugs_mode = serializers.ChoiceField(
        choices=("combined", "isolated", "both"), required=False, default="combined"
    )
    merge_threshold_ft = serializers.FloatField(required=False, default=500.0)
    gau_file = serializers.FileField(required=False, allow_null=True)
    w2_file = serializers.FileField(required=False, allow_null=True)
    w15_file = serializers.FileField(required=False, allow_null=True)
    schematic_file = serializers.FileField(required=False, allow_null=True)
    formation_tops_file = serializers.FileField(required=False, allow_null=True)

    def validate_api10(self, value: str) -> str:
        import re

        digits = re.sub(r"\D+", "", str(value or ""))
        if len(digits) != 10:
            raise serializers.ValidationError("api10 must contain exactly 10 digits")
        return digits

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        # If user opted to provide a GAU override, require the file in the same request
        if attrs.get("use_gau_override_if_invalid"):
            if not attrs.get("gau_file"):
                raise serializers.ValidationError(
                    {"gau_file": "Required when use_gau_override_if_invalid is true"}
                )
        # Validate at least one file when in user_files mode
        if attrs.get("input_mode") == "user_files":
            if not any(attrs.get(k) for k in ("w2_file", "w15_file", "gau_file", "schematic_file", "formation_tops_file")):
                raise serializers.ValidationError(
                    {"input_mode": "user_files requires at least one file (W-2/W-15/GAU/Schematic/Formation Tops)"}
                )
        # Ensure merge threshold is non-negative
        mt = attrs.get("merge_threshold_ft")
        try:
            if mt is not None and float(mt) < 0:
                raise serializers.ValidationError({"merge_threshold_ft": "Must be >= 0"})
        except (TypeError, ValueError):
            raise serializers.ValidationError({"merge_threshold_ft": "Must be a number"})
        return attrs


class W3APlanSerializer(serializers.Serializer):
    api = serializers.CharField()
    jurisdiction = serializers.CharField(required=False, allow_null=True)
    district = serializers.CharField(required=False, allow_null=True)
    county = serializers.CharField(required=False, allow_null=True)
    field = serializers.CharField(required=False, allow_null=True)
    field_resolution = serializers.JSONField(required=False)
    formation_tops_detected = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    formations_targeted = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    rounding = serializers.CharField(required=False, allow_null=True)
    steps = serializers.ListField(child=serializers.JSONField(), required=False)
    plan_notes = serializers.ListField(child=serializers.CharField(), required=False)
    materials_totals = serializers.JSONField(required=False)
    debug_overrides = serializers.JSONField(required=False)
    rrc_export = serializers.ListField(child=serializers.JSONField(), required=False)
    violations = serializers.ListField(child=serializers.JSONField(), required=False)
    gau_protect_intervals = serializers.ListField(
        child=serializers.JSONField(), required=False
    )


class W3APlanVariantsSerializer(serializers.Serializer):
    combined = W3APlanSerializer(required=False)
    isolated = W3APlanSerializer(required=False)



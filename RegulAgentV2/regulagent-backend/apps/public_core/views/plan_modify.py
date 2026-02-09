from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from apps.public_core.models import WellRegistry, PlanSnapshot
from apps.tenant_overlay.models.plan_modification import PlanModification
import re


class PlanModifyView(APIView):
    """Apply manual operations to modify a plan (combine_plugs, replace_cibp, etc.)."""

    def post(self, request, api: str):
        api_digits = re.sub(r"\D+", "", str(api or ""))
        if not api_digits:
            return Response({"detail": "API number is required"}, status=status.HTTP_400_BAD_REQUEST)

        import json
        op = (request.data or {}).get("operation")
        params = (request.data or {}).get("params") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                params = {}
        if not op:
            return Response({"detail": "operation is required"}, status=status.HTTP_400_BAD_REQUEST)

        well = WellRegistry.objects.filter(api14__icontains=api_digits[-8:]).first()
        if not well:
            return Response({"detail": "Well not found"}, status=status.HTTP_404_NOT_FOUND)

        base = (
            PlanSnapshot.objects
            .filter(well=well)
            .order_by('-created_at')
            .first()
        )
        if not base or not isinstance(base.payload, dict):
            return Response({"detail": "No plan snapshot found for this well"}, status=status.HTTP_404_NOT_FOUND)

        plan_payload: Dict[str, Any]
        if isinstance(base.payload.get("variants"), dict):
            variants = base.payload["variants"]
            plan_payload = variants.get("combined") or variants.get("isolated") or {}
        else:
            plan_payload = base.payload

        steps: List[Dict[str, Any]] = list(plan_payload.get("steps") or [])
        original_steps = [dict(s) for s in steps]

        diff = {"removed_steps": [], "added_steps": [], "updated_steps": []}  # type: ignore

        def _find_indices_by_type(t: str) -> List[int]:
            return [i for i, s in enumerate(steps) if s.get("type") == t]

        if op == "combine_plugs":
            # Params: indices: [i,j] or types: ["cement_plug","cement_plug"] and optional merge_all_overlaps=true
            idxs = params.get("indices") or []
            if isinstance(idxs, list) and len(idxs) == 2:
                i, j = sorted([int(idxs[0]), int(idxs[1])])
                if 0 <= i < len(steps) and 0 <= j < len(steps):
                    a, b = steps[i], steps[j]
                    if a.get("type") == "cement_plug" and b.get("type") == "cement_plug":
                        top = min(x for x in [a.get("top_ft"), b.get("top_ft")] if isinstance(x, (int, float)))
                        bot = max(x for x in [a.get("bottom_ft"), b.get("bottom_ft")] if isinstance(x, (int, float)))
                        merged = dict(a)
                        merged["top_ft"] = float(top)
                        merged["bottom_ft"] = float(bot)
                        # naive sacks sum if present
                        s1 = a.get("sacks") or 0; s2 = b.get("sacks") or 0
                        if isinstance(s1, (int, float)) and isinstance(s2, (int, float)):
                            merged["sacks"] = float(s1) + float(s2)
                        diff["removed_steps"].append(a)
                        diff["removed_steps"].append(b)
                        steps.pop(j)
                        steps.pop(i)
                        steps.insert(i, merged)
                        diff["added_steps"].append(merged)
                    else:
                        return Response({"detail": "combine_plugs currently supports cement_plug types only"}, status=status.HTTP_400_BAD_REQUEST)
                else:
                    return Response({"detail": "indices out of range"}, status=status.HTTP_400_BAD_REQUEST)
            else:
                return Response({"detail": "combine_plugs requires params.indices [i,j]"}, status=status.HTTP_400_BAD_REQUEST)

        elif op == "replace_cibp_with_long_plug":
            # Remove CIBP and cap, insert a cement_plug across producing interval if available in plan
            cibp_idx = next((i for i, s in enumerate(steps) if s.get("type") in ("bridge_plug", "CIBP")), None)
            cap_idx = next((i for i, s in enumerate(steps) if s.get("type") in ("bridge_plug_cap", "cibp_cap", "CIBP cap")), None)
            prod = plan_payload.get("producing_interval_ft") or None
            if cibp_idx is None or cap_idx is None or not isinstance(prod, list) or len(prod) != 2:
                return Response({"detail": "Cannot locate CIBP/cap or producing interval in plan"}, status=status.HTTP_400_BAD_REQUEST)
            top = float(min(prod)); bot = float(max(prod))
            # remove higher index first
            for idx in sorted([cibp_idx, cap_idx], reverse=True):
                diff["removed_steps"].append(steps[idx])
                steps.pop(idx)
            new_step = {
                "type": "cement_plug",
                "top_ft": top,
                "bottom_ft": bot,
                "sacks": None,
                "regulatory_basis": ["tenant.edit:replace_cibp_with_long_plug"],
                "details": {"note": "User edit: replace CIBP with long plug"},
            }
            steps.append(new_step)
            diff["added_steps"].append(new_step)

        else:
            return Response({"detail": f"unsupported operation: {op}"}, status=status.HTTP_400_BAD_REQUEST)

        # Compose new plan payload
        new_plan = dict(plan_payload)
        new_plan["steps"] = steps

        # Persist snapshot and modification
        try:
            ps = PlanSnapshot.objects.create(
                well=well,
                plan_id=f"{well.api14}:{op}",
                kind="post_edit",
                payload=new_plan,
                kernel_version=str((base.payload or {}).get("kernel_version") or ""),
                policy_id=base.policy_id,
                overlay_id=base.overlay_id,
                extraction_meta=base.extraction_meta,
                # Post-edit snapshots are private (tenant's WIP modifications)
                visibility=PlanSnapshot.VISIBILITY_PRIVATE,
                tenant_id=None,  # Future: populate from request.user when auth enabled
            )
            PlanModification.objects.create(
                original_snapshot=base,
                modified_snapshot=ps,
                modification_type=op,
                request_payload={"params": params},
                applied_ops=[{"op": op, "params": params}],
                diff_summary=diff,
            )
        except Exception:
            return Response({"detail": "Failed to persist modification"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({
            "api": api_digits,
            "plan_id": ps.plan_id,
            "snapshot_id": ps.id,
            "diff": diff,
            "steps": steps,
        }, status=status.HTTP_200_OK)



from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from apps.public_core.models import WellRegistry, PlanSnapshot
from apps.tenant_overlay.models.plan_modification import PlanModification
import re


class PlanModifyAIView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, api: str):
        api_digits = re.sub(r"\D+", "", str(api or ""))
        if not api_digits:
            return Response({"detail": "API number is required"}, status=status.HTTP_400_BAD_REQUEST)

        message = (request.data or {}).get("message") or ""
        strict = bool((request.data or {}).get("strict", True))
        apply_flag = bool((request.data or {}).get("apply", False))
        if not message:
            return Response({"detail": "message is required"}, status=status.HTTP_400_BAD_REQUEST)

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

        # Heuristic proposal (MVP): infer ops from message keywords
        operations, rationale = self._propose_operations(message, steps)

        # Preview apply (no persistence in this file)
        preview_steps, diff = self._apply_preview(steps, operations)

        if apply_flag:
            new_plan = dict(plan_payload)
            new_plan["steps"] = preview_steps
            try:
                ps = PlanSnapshot.objects.create(
                    well=well,
                    plan_id=f"{well.api14}:post_edit",
                    kind="post_edit",
                    payload=new_plan,
                    kernel_version=base.kernel_version,
                    policy_id=base.policy_id,
                    overlay_id=base.overlay_id,
                    extraction_meta=base.extraction_meta,
                    # Post-edit snapshots are private (tenant's WIP modifications)
                    visibility=PlanSnapshot.VISIBILITY_PRIVATE,
                    tenant_id=None,  # Future: populate from request.user when auth enabled
                )
                modification_saved = True
                try:
                    try:
                        PlanModification.objects.create(
                            original_snapshot=base,
                            modified_snapshot=ps,
                            modification_type="ai_modify",
                            request_payload={"message": message, "strict": strict},
                            applied_ops=operations,
                            diff_summary=diff,
                            ai_rationale=rationale,
                            risk_score=0.1,
                        )
                    except TypeError:
                        PlanModification.objects.create(
                            plan_snapshot=ps,
                            plan_id=ps.plan_id,
                            operation="ai_modify",
                            request_payload={"message": message, "strict": strict, "applied_ops": operations},
                            result_diff=diff,
                        )
                except Exception:
                    modification_saved = False
            except Exception as e:
                return Response({"detail": "Failed to persist modification", "error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            return Response({
                "applied": True,
                "snapshot_id": str(ps.id),
                "plan_id": ps.plan_id,
                "operations": operations,
                "ai_rationale": rationale,
                "diff": diff,
                "modification_saved": modification_saved,
            }, status=status.HTTP_200_OK)

        return Response({
            "operations": operations,
            "ai_rationale": rationale,
            "risk_score": 0.1,
            "preview": {"diff": diff, "steps": preview_steps},
        }, status=status.HTTP_200_OK)

    def _propose_operations(self, message: str, steps: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], str]:
        msg = message.lower()
        ops: List[Dict[str, Any]] = []
        rationale = []

        # Replace CIBP → long plug across producing interval
        if ("replace" in msg and "cibp" in msg) or ("long plug" in msg):
            ops.append({"op": "replace_cibp_with_long_plug", "params": {}})
            rationale.append("Replace CIBP with continuous cement plug across producing interval as requested.")

        # Combine plugs (try last two cement_plug steps)
        if "combine" in msg and "plug" in msg:
            cement_indices = [i for i, s in enumerate(steps) if s.get("type") == "cement_plug"]
            if len(cement_indices) >= 2:
                i, j = cement_indices[-2], cement_indices[-1]
                ops.append({"op": "combine_plugs", "params": {"indices": [i, j]}})
                rationale.append(f"Combine cement plugs at indices {i} and {j} due to overlap/adjacency.")

        if not ops:
            rationale.append("No specific operation inferred; clarify indices or intent.")

        return ops, "; ".join(rationale)

    def _apply_preview(self, steps: List[Dict[str, Any]], operations: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        import math
        from copy import deepcopy

        new_steps = deepcopy(steps)
        diff = {"removed_steps": [], "added_steps": [], "updated_steps": []}  # type: ignore

        for op in operations:
            name = op.get("op")
            params = op.get("params") or {}

            if name == "combine_plugs":
                idxs = params.get("indices") or []
                if isinstance(idxs, list) and len(idxs) == 2:
                    i, j = sorted([int(idxs[0]), int(idxs[1])])
                    if 0 <= i < len(new_steps) and 0 <= j < len(new_steps):
                        a, b = new_steps[i], new_steps[j]
                        if a.get("type") == "cement_plug" and b.get("type") == "cement_plug":
                            tops = [x for x in [a.get("top_ft"), b.get("top_ft")] if isinstance(x, (int, float))]
                            bots = [x for x in [a.get("bottom_ft"), b.get("bottom_ft")] if isinstance(x, (int, float))]
                            if tops and bots:
                                top = float(min(tops))
                                bot = float(max(bots))
                                merged = dict(a)
                                merged["top_ft"] = top
                                merged["bottom_ft"] = bot
                                s1 = a.get("sacks") or 0; s2 = b.get("sacks") or 0
                                if isinstance(s1, (int, float)) and isinstance(s2, (int, float)):
                                    merged["sacks"] = float(s1) + float(s2)
                                diff["removed_steps"].append(a)
                                diff["removed_steps"].append(b)
                                new_steps.pop(j)
                                new_steps.pop(i)
                                new_steps.insert(i, merged)
                                diff["added_steps"].append(merged)
                        # else: ignore non-cement types in preview
                # else: ignore malformed indices in preview

            elif name == "replace_cibp_with_long_plug":
                cibp_idx = next((ix for ix, s in enumerate(new_steps) if s.get("type") in ("bridge_plug", "CIBP")), None)
                cap_idx = next((ix for ix, s in enumerate(new_steps) if s.get("type") in ("bridge_plug_cap", "cibp_cap", "CIBP cap")), None)
                prod = params.get("producing_interval_ft")  # allow override; else try from existing steps metadata
                if prod is None:
                    prod = None  # keep preview conservative without plan-wide metadata here
                if cibp_idx is not None and cap_idx is not None and isinstance(prod, list) and len(prod) == 2:
                    top = float(min(prod)); bot = float(max(prod))
                    for idx in sorted([cibp_idx, cap_idx], reverse=True):
                        diff["removed_steps"].append(new_steps[idx])
                        new_steps.pop(idx)
                    new_step = {
                        "type": "cement_plug",
                        "top_ft": top,
                        "bottom_ft": bot,
                        "sacks": None,
                        "regulatory_basis": ["tenant.edit:replace_cibp_with_long_plug"],
                        "details": {"note": "Preview: replace CIBP with long plug"},
                    }
                    new_steps.append(new_step)
                    diff["added_steps"].append(new_step)
                # else: if we can't determine interval, skip in preview

            # else: unrecognized op names are ignored in preview

        return new_steps, diff



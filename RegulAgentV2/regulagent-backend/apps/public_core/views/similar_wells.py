from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from apps.public_core.models import WellRegistry
from apps.public_core.models.document_vector import DocumentVector
from apps.public_core.models import ExtractedDocument, PlanSnapshot


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8  # miles
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c


class SimilarWellsView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        api = request.query_params.get('api')
        if not api:
            return Response({"detail": "api is required"}, status=status.HTTP_400_BAD_REQUEST)

        radius_mi = float(request.query_params.get('radius_mi', 0.5))
        months = int(request.query_params.get('months', 24))  # reserved for future outcome/recency filters
        operator_boost = float(request.query_params.get('operator_boost', 1.2))
        field_boost = float(request.query_params.get('field_boost', 1.1))
        use_vector = str(request.query_params.get('use_vector', 'false')).lower() == 'true'
        vector_weight = float(request.query_params.get('vector_weight', 0.4))
        # Structured weights
        w_arch = float(request.query_params.get('w_architecture', 0.40))
        w_pattern = float(request.query_params.get('w_pattern', 0.45))
        w_context = float(request.query_params.get('w_context', 0.10))
        w_quality = float(request.query_params.get('w_quality', 0.05))
        alpha_geo = float(request.query_params.get('alpha_geo', 0.5))
        state = request.query_params.get('state') or 'TX'
        county = request.query_params.get('county')
        field = request.query_params.get('field')
        require_county = str(request.query_params.get('require_county', 'auto')).lower()
        require_field = str(request.query_params.get('require_field', 'auto')).lower()
        operator = request.query_params.get('operator')
        k = int(request.query_params.get('k', 20))

        api_digits = re.sub(r"\D+", "", str(api))
        src = WellRegistry.objects.filter(api14__icontains=api_digits[-8:]).first()
        if not src or src.lat is None or src.lon is None:
            return Response({"detail": "source well missing or has no lat/lon"}, status=status.HTTP_404_NOT_FOUND)

        # Coarse bbox prefilter (~1 degree ~ 69 miles)
        deg = radius_mi / 69.0
        lat_min = float(src.lat) - deg
        lat_max = float(src.lat) + deg
        lon_min = float(src.lon) - deg
        lon_max = float(src.lon) + deg

        qs = WellRegistry.objects.exclude(id=src.id).filter(state=state, lat__isnull=False, lon__isnull=False, lat__gte=lat_min, lat__lte=lat_max, lon__gte=lon_min, lon__lte=lon_max)

        # Helpers (defined before use)
        def _latest_w2(api_num: str) -> Optional[ExtractedDocument]:
            return (
                ExtractedDocument.objects
                .filter(api_number=api_num, document_type='w2')
                .order_by('-created_at')
                .first()
            )

        def _latest_gau(api_num: str) -> Optional[ExtractedDocument]:
            return (
                ExtractedDocument.objects
                .filter(api_number=api_num, document_type='gau')
                .order_by('-created_at')
                .first()
            )

        def _latest_snapshot(well: WellRegistry) -> Optional[PlanSnapshot]:
            return (
                PlanSnapshot.objects
                .filter(well=well)
                .order_by('-created_at')
                .first()
            )

        # Derive source county/field
        src_snap = _latest_snapshot(src)
        src_field = (src.field_name or '') or ((src_snap and isinstance(src_snap.payload, dict) and (src_snap.payload.get('field') or '')) or '')
        src_county = (src.county or '') or ((src_snap and isinstance(src_snap.payload, dict) and (src_snap.payload.get('county') or '')) or '')

        # Enforce county/field by default for TX (unless explicitly disabled)
        def _enforce(v):
            # 'true' -> enforce, 'false' -> skip, 'auto' -> enforce when TX
            if v == 'true':
                return True
            if v == 'false':
                return False
            return (state.upper() == 'TX')

        if county:
            qs = qs.filter(county__iexact=county)
        elif _enforce(require_county) and src_county:
            qs = qs.filter(county__iexact=src_county)

        if field:
            qs = qs.filter(field_name__iexact=field)
        elif _enforce(require_field) and src_field:
            qs = qs.filter(field_name__iexact=src_field)
        # Boost same-operator later in scoring

        # TODO: incorporate district/field/operator once persisted on WellRegistry

        

        def _parse_arch(w2: Optional[ExtractedDocument]) -> Dict[str, Any]:
            out: Dict[str, Any] = {
                'strings': 0,
                'shoe_depths': [],
                'casing_ids': [],
                'td': None,
                'tubing_depth': None,
                'uqw': None,
                'formations': set(),
            }
            if not w2 or not isinstance(w2.json_data, dict):
                return out
            d = w2.json_data
            cr = d.get('casing_record') or []
            out['strings'] = len(cr) if isinstance(cr, list) else 0
            # Shoe depths and casing IDs (nominal) for common strings
            def _to_f(x):
                try:
                    return float(x)
                except Exception:
                    return None
            shoe_keys = ('shoe_depth_ft','setting_depth_ft','bottom_ft')
            for row in cr:
                if not isinstance(row, dict):
                    continue
                # shoe depth
                sd = None
                for k in shoe_keys:
                    if row.get(k) is not None:
                        sd = _to_f(row.get(k)); break
                if sd is not None:
                    out['shoe_depths'].append(sd)
                # casing id approx from size
                sz = row.get('size_in') or row.get('casing_size_in')
                if sz is not None:
                    try:
                        out['casing_ids'].append(float(sz))
                    except Exception:
                        pass
            # Tubing record
            tr = d.get('tubing_record') or []
            if isinstance(tr, list) and tr:
                td = tr[0].get('bottom_ft') or tr[0].get('top_ft')
                out['tubing_depth'] = _to_f(td)
            # Producing interval TD proxy
            prod = d.get('producing_injection_disposal_interval') or {}
            if isinstance(prod, dict):
                pf = _to_f(prod.get('from_ft'))
                pt = _to_f(prod.get('to_ft'))
                tdv = max([x for x in (pf, pt) if x is not None], default=None)
                out['td'] = tdv
            # Formation tops
            fr = d.get('formation_record') or []
            if isinstance(fr, list):
                for rec in fr:
                    name = (rec.get('formation') or '').strip().lower()
                    if name:
                        out['formations'].add(name)
            return out

        def _arch_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
            import math
            if not a or not b:
                return 0.0
            s_strings = 1.0 - min(abs((a.get('strings') or 0) - (b.get('strings') or 0)) / 3.0, 1.0)
            # Shoe depths proximity (pairwise min size)
            sd_a = sorted([x for x in (a.get('shoe_depths') or []) if isinstance(x, (int,float))])
            sd_b = sorted([x for x in (b.get('shoe_depths') or []) if isinstance(x, (int,float))])
            n = min(len(sd_a), len(sd_b), 3)
            s_shoes = 0.0
            for i in range(n):
                s_shoes += math.exp(-abs(sd_a[i] - sd_b[i]) / 500.0)
            s_shoes = s_shoes / n if n > 0 else 0.0
            # Casing ID proximity
            id_a = sorted([x for x in (a.get('casing_ids') or []) if isinstance(x, (int,float))])
            id_b = sorted([x for x in (b.get('casing_ids') or []) if isinstance(x, (int,float))])
            m = min(len(id_a), len(id_b), 3)
            s_id = 0.0
            for i in range(m):
                s_id += math.exp(-abs(id_a[i] - id_b[i]) / 0.25)
            s_id = s_id / m if m > 0 else 0.0
            # TD proximity
            td_a = a.get('td'); td_b = b.get('td')
            s_td = math.exp(-abs((td_a or 0) - (td_b or 0)) / 1500.0) if (td_a and td_b) else 0.0
            return max(0.0, min(1.0, 0.4*s_strings + 0.35*s_shoes + 0.25*s_id + 0.0*s_td))

        def _pattern_signature(snap: Optional[PlanSnapshot]) -> Tuple[List[str], List[Tuple[float,float,str,int]]]:
            if not snap or not isinstance(snap.payload, dict):
                return [], []
            payload = snap.payload
            plan = payload.get('variants', {}).get('combined') or payload
            steps = plan.get('steps') or []
            types = [ (s.get('type') or '') for s in steps ]
            # intervals normalized by max bottom
            bottoms = [s.get('bottom_ft') for s in steps if isinstance(s.get('bottom_ft'), (int,float))]
            maxb = max(bottoms) if bottoms else None
            sig: List[Tuple[float,float,str,int]] = []
            for s in steps:
                t = s.get('type') or ''
                top = s.get('top_ft'); bot = s.get('bottom_ft')
                cls = (s.get('cement_class') or (s.get('details') or {}).get('cement_class') or '')
                tag = 1 if ((s.get('details') or {}).get('verification') or {}).get('action') == 'TAG' else 0
                if isinstance(top, (int,float)) and isinstance(bot, (int,float)) and maxb:
                    sig.append( (float(top)/maxb, float(bot)/maxb, str(cls), tag) )
            return types, sig

        def _lcs_norm(a: List[str], b: List[str]) -> float:
            if not a or not b:
                return 0.0
            n, m = len(a), len(b)
            dp = [[0]*(m+1) for _ in range(n+1)]
            for i in range(1, n+1):
                for j in range(1, m+1):
                    if a[i-1] == b[j-1]:
                        dp[i][j] = dp[i-1][j-1] + 1
                    else:
                        dp[i][j] = max(dp[i-1][j], dp[i][j-1])
            l = dp[n][m]
            return l / max(n, m)

        def _interval_iou(sig_a: List[Tuple[float,float,str,int]], sig_b: List[Tuple[float,float,str,int]]) -> float:
            # simple average of best IoU for same-type plugs, using normalized intervals
            if not sig_a or not sig_b:
                return 0.0
            ious: List[float] = []
            for ta, ba, ca, taga in sig_a:
                best = 0.0
                for tb, bb, cb, tagb in sig_b:
                    # compute IoU on [min,max]
                    lo = max(min(ta,ba), min(tb,bb))
                    hi = min(max(ta,ba), max(tb,bb))
                    inter = max(0.0, hi - lo)
                    union = max(ta,ba) - min(ta,ba) + max(tb,bb) - min(tb,bb) - inter
                    if union > 0:
                        best = max(best, inter/union)
                ious.append(best)
            return sum(ious)/len(ious) if ious else 0.0

        def _pattern_similarity(sa: Tuple[List[str], List[Tuple[float,float,str,int]]], sb: Tuple[List[str], List[Tuple[float,float,str,int]]]) -> float:
            types_a, sig_a = sa; types_b, sig_b = sb
            lcs = _lcs_norm(types_a, types_b)
            iou = _interval_iou(sig_a, sig_b)
            return max(0.0, min(1.0, 0.6*lcs + 0.4*iou))

        def _context_similarity(src_ctx: Dict[str, Any], nb_ctx: Dict[str, Any]) -> float:
            score = 0.0; cnt = 0
            for k in ('district','county','field'):
                a = (src_ctx.get(k) or '').strip().lower(); b = (nb_ctx.get(k) or '').strip().lower()
                if a or b:
                    cnt += 1
                    score += 1.0 if a == b else 0.0
            return score/cnt if cnt else 0.0

        neighbors: List[Dict[str, Any]] = []
        for w in qs[:1000]:  # safety cap
            d = haversine_miles(float(src.lat), float(src.lon), float(w.lat), float(w.lon))
            if d <= radius_mi:
                score = 1.0 / (1.0 + d)
                if operator and (w.operator_name or '').strip().lower() == operator.strip().lower():
                    score *= operator_boost
                if field and (w.field_name or '').strip().lower() == field.strip().lower():
                    score *= field_boost
                nb_api = w.api14
                # Structured features
                src_w2 = _latest_w2(src.api14)
                nb_w2 = _latest_w2(nb_api)
                arch_a = _parse_arch(src_w2); arch_b = _parse_arch(nb_w2)
                s_arch = _arch_similarity(arch_a, arch_b)
                # Pattern from latest snapshots
                snap_a = _latest_snapshot(src)
                snap_b = _latest_snapshot(w)
                pat_a = _pattern_signature(snap_a); pat_b = _pattern_signature(snap_b)
                s_pat = _pattern_similarity(pat_a, pat_b)
                # Context
                ctx_a = {"district": getattr(snap_a and snap_a.payload, 'get', lambda k: None)('district') if snap_a else None,
                         "county": getattr(snap_a and snap_a.payload, 'get', lambda k: None)('county') if snap_a else src.county,
                         "field": getattr(snap_a and snap_a.payload, 'get', lambda k: None)('field') if snap_a else None}
                ctx_b = {"district": getattr(snap_b and snap_b.payload, 'get', lambda k: None)('district') if snap_b else None,
                         "county": getattr(snap_b and snap_b.payload, 'get', lambda k: None)('county') if snap_b else w.county,
                         "field": getattr(snap_b and snap_b.payload, 'get', lambda k: None)('field') if snap_b else None}
                s_ctx = _context_similarity(ctx_a, ctx_b)
                # Quality factor (optional; default 1)
                q = 1.0
                s_struct = max(0.0, min(1.0, w_arch*s_arch + w_pattern*s_pat + w_context*s_ctx + w_quality*q))
                base_geo = score
                base_structured = alpha_geo*base_geo + (1.0 - alpha_geo)*s_struct
                neighbors.append({
                    "api14": w.api14,
                    "state": w.state,
                    "county": w.county,
                    "distance_mi": round(d, 3),
                    "operator_name": w.operator_name,
                    "field_name": w.field_name,
                    "score": round(base_structured, 6),
                    "components": {
                        "base_geo": round(base_geo, 6),
                        "arch": round(s_arch, 6),
                        "pattern": round(s_pat, 6),
                        "context": round(s_ctx, 6),
                    }
                })

        # sort by score desc, then distance asc
        neighbors.sort(key=lambda x: (-x["score"], x["distance_mi"]))  # type: ignore

        # Optional vector rerank (blend cosine similarity with current score)
        if use_vector and neighbors:
            try:
                def _avg_vec(vecs: List[List[float]]) -> Optional[List[float]]:
                    if not vecs:
                        return None
                    dim = len(vecs[0])
                    acc = [0.0] * dim
                    for v in vecs:
                        if v is None or len(v) != dim:
                            continue
                        for i in range(dim):
                            acc[i] += float(v[i])
                    n = float(len(vecs))
                    if n == 0:
                        return None
                    return [x / n for x in acc]

                def _cos(a: List[float], b: List[float]) -> float:
                    num = 0.0; da = 0.0; db = 0.0
                    for i in range(len(a)):
                        ai = float(a[i]); bi = float(b[i])
                        num += ai * bi; da += ai * ai; db += bi * bi
                    if da <= 0 or db <= 0:
                        return 0.0
                    import math
                    return max(-1.0, min(1.0, num / (math.sqrt(da) * math.sqrt(db))))

                # Build source well profile embedding (avg across its vectors)
                src_vecs = list(DocumentVector.objects.filter(well=src).values_list('embedding', flat=True)[:1000])
                src_avg = _avg_vec([list(v) for v in src_vecs if v is not None]) if src_vecs else None
                if src_avg is not None:
                    # Compute avg embedding for each neighbor well and blend the score
                    api14_to_vec: Dict[str, List[float]] = {}
                    for n in neighbors:
                        # Resolve neighbor well by API14 suffix
                        w = WellRegistry.objects.filter(api14=n["api14"]).first()
                        if not w:
                            continue
                        vecs = list(DocumentVector.objects.filter(well=w).values_list('embedding', flat=True)[:500])
                        avg = _avg_vec([list(v) for v in vecs if v is not None]) if vecs else None
                        if avg is None:
                            continue
                        api14_to_vec[n["api14"]] = avg

                    for n in neighbors:
                        v = api14_to_vec.get(n["api14"])  # type: ignore
                        if v is None:
                            n["vector_sim"] = None
                            n["blended_score"] = n["score"]
                        else:
                            sim = _cos(src_avg, v)
                            n["vector_sim"] = round(sim, 6)
                            base = float(n["score"])  # distance/boost score in 0..1
                            # Normalize sim (-1..1) to (0..1)
                            sim01 = (sim + 1.0) / 2.0
                            n["blended_score"] = round((1.0 - vector_weight) * base + vector_weight * sim01, 6)

                    neighbors.sort(key=lambda x: (-x.get("blended_score", x["score"]), x["distance_mi"]))  # type: ignore
            except Exception:
                # If vector rerank fails, keep original ordering
                pass
        neighbors = neighbors[:k]

        return Response({
            "api": src.api14,
            "lat": float(src.lat),
            "lon": float(src.lon),
            "radius_mi": radius_mi,
            "months": months,
            "operator_boost": operator_boost,
            "field_boost": field_boost,
            "rerank": ("vector" if use_vector else "off"),
            "vector_weight": vector_weight,
            "count": len(neighbors),
            "neighbors": neighbors,
        }, status=status.HTTP_200_OK)



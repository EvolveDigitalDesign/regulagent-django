from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from typing import Any, Dict, List

from apps.policy.services.loader import get_effective_policy
from apps.kernel.services.policy_kernel import plan_from_facts


class AdvisorySanityCheckView(APIView):
    def post(self, request):
        payload = request.data or {}
        facts = payload.get('facts') or {}
        district = (facts.get('district') or {}).get('value') if isinstance(facts.get('district'), dict) else facts.get('district')
        county = (facts.get('county') or {}).get('value') if isinstance(facts.get('county'), dict) else facts.get('county')
        policy = get_effective_policy(district=str(district) if district else None, county=str(county) if county else None)
        plan = plan_from_facts(facts, policy)

        findings: List[Dict[str, Any]] = []
        eff = policy.get('effective') or {}
        prefs = eff.get('preferences') or {}
        chart = (prefs.get('plugging_chart') or {})
        # naive comparison: if a surface shoe step exists, compare sacks to casing_open_hole Surface depth recommendation if available
        shoe = next((s for s in plan.get('steps', []) if s.get('type') == 'surface_casing_shoe_plug'), None)
        if shoe and chart.get('casing_open_hole'):
            rows = chart['casing_open_hole'].get('data') or []
            surface_row = next((r for r in rows if str(r.get('depth')).lower().startswith('surface')), None)
            if surface_row:
                rec_values = surface_row.get('values') or []
                # choose an approximate bucket by index 0 if unknown geometry
                try:
                    rec = rec_values[0]
                    sacks = (shoe.get('materials') or {}).get('slurry', {}).get('sacks')
                    if isinstance(rec, (int, float)) and isinstance(sacks, (int, float)):
                        if abs(float(sacks) - float(rec)) > 10:  # arbitrary delta threshold for demo
                            findings.append({
                                'code': 'advisory.sacks_vs_chart_delta',
                                'severity': 'minor',
                                'message': f'sacks {sacks} differ from chart {rec} (surface) by >10',
                                'context': {'chart_rec_sacks': rec, 'computed_sacks': sacks},
                            })
                except Exception:
                    pass
        return Response({'plan': plan, 'findings': findings}, status=status.HTTP_200_OK)

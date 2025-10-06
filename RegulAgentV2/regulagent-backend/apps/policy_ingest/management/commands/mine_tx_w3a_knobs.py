import re
from typing import Dict, List

from django.core.management.base import BaseCommand


PATTERNS = {
    # numeric depths/lengths
    'min_surface_cap_ft': re.compile(r"(at least|not less than)\s+(?P<val>\d{1,3})\s*(feet|ft)", re.I),
    'tag_wait_hours': re.compile(r"(tag)[^\n]{0,60}(?P<val>\d{1,2})\s*(hours|hrs?)", re.I),
    # factors/percentages
    'squeeze_factor_default': re.compile(r"squeeze[^\n]{0,80}(factor|multiple)[^\n]{0,40}(?P<val>\d(\.\d)?)", re.I),
    'open_hole_excess_default': re.compile(r"open\s+hole[^\n]{0,60}(excess|overage)[^\n]{0,40}(?P<val>\d{1,3})\s*%", re.I),
}

# boolean cues (presence only)
BOOL_CUES = {
    'never_below_cibp': re.compile(r"(never|not)\s+.*below\s+.*(CIBP|cast\s+iron\s+bridge\s+plug)", re.I),
    'duqw_isolation_required': re.compile(r"(usable\s+quality\s+water|fresh\s+water).*?(isolate|cement|protect)", re.I),
}


class Command(BaseCommand):
    help = "Mine candidate TX W-3A knobs from policy_sections (regex-based), print JSON for review."

    def add_arguments(self, parser):
        parser.add_argument('--version-tag', default='2025-Q4')

    def handle(self, *args, **options):
        import json
        from apps.policy_ingest.models import PolicyRule, PolicySection

        version_tag = options['version_tag']
        rule = PolicyRule.objects.filter(rule_id='tx.tac.16.3.14', version_tag=version_tag).first()
        if not rule:
            self.stdout.write("No §3.14 rule found for version tag")
            return

        sections = PolicySection.objects.filter(rule=rule, version_tag=version_tag).order_by('order_idx')
        findings: Dict[str, Dict] = {}
        # subsection allowlists per knob
        allow: Dict[str, List[str]] = {
            'surface_casing_shoe_plug_min_ft': ['e(2)'],
            'uqw_isolation_plug_min_ft': ['g(1)'],
            'cement_above_cibp_min_ft': ['g(3)'],
        }

        for s in sections:
            text = s.text or ''
            for key, pat in PATTERNS.items():
                # Skip definitions and enforce path allowlists where defined
                if s.path.startswith('a'):
                    continue
                if key in allow and s.path not in allow[key]:
                    continue
                m = pat.search(text)
                if m:
                    raw = m.group('val') if m.groupdict().get('val') else None
                    if raw is None:
                        continue
                    try:
                        val = int(raw)
                    except Exception:
                        continue
                    item = findings.setdefault(key, {"proposed_value": None, "hits": []})
                    item["proposed_value"] = item["proposed_value"] or val
                    item["hits"].append({
                        "section_id": s.id,
                        "path": s.path,
                        "text_snippet": text[:200],
                        "value": val,
                    })
            # Hardening: explicit subsection parsing with strong phrases
            if s.path == 'e(2)':
                m100 = re.search(r"minimum\s+of\s+(?P<val>100)\s*(feet|ft)", text, re.I)
                if m100:
                    item = findings.setdefault('surface_casing_shoe_plug_min_ft', {"proposed_value": None, "hits": []})
                    if not item["proposed_value"]:
                        item["proposed_value"] = 100
                    item["hits"].append({"section_id": s.id, "path": s.path, "text_snippet": text[:200], "value": 100})
            if s.path == 'g(1)':
                m100 = re.search(r"(minimum(\s+of)?|at\s+least|not\s+less\s+than)\s+(?P<val>100)\s*(feet|ft)", text, re.I)
                below = re.search(r"(?P<val>50)\s*(feet|ft)\s+below", text, re.I)
                above = re.search(r"(?P<val>50)\s*(feet|ft)\s+above", text, re.I)
                if m100 and below and above:
                    item = findings.setdefault('uqw_isolation_plug', {"proposed_value": None, "hits": []})
                    item["proposed_value"] = {"min_len_ft": 100, "below_ft": 50, "above_ft": 50}
                    item["hits"].append({"section_id": s.id, "path": s.path, "text_snippet": text[:200], "value": [100, 50, 50]})
            if s.path == 'g(3)':
                m20 = re.search(r"at\s+least\s+(?P<val>20)\s*(feet|ft)\s+(of\s+cement|cement)\s+.*(on\s+top|placed\s+on\s+top)", text, re.I)
                if m20:
                    item = findings.setdefault('cement_above_cibp_min_ft', {"proposed_value": None, "hits": []})
                    if not item["proposed_value"]:
                        item["proposed_value"] = 20
                    item["hits"].append({"section_id": s.id, "path": s.path, "text_snippet": text[:200], "value": 20})
            # boolean cues
            for key, pat in BOOL_CUES.items():
                if pat.search(text):
                    item = findings.setdefault(key, {"proposed_value": True, "hits": []})
                    item["hits"].append({
                        "section_id": s.id,
                        "path": s.path,
                        "text_snippet": text[:200],
                    })

        # Force cap_above_highest_perf_ft to null at base
        findings['cap_above_highest_perf_ft'] = {"proposed_value": None, "reason": "no_base_rule", "hits": []}
        # Drop ambiguous legacy knob if present
        findings.pop('min_surface_cap_ft', None)
        self.stdout.write(json.dumps(findings, indent=2))


